import os
import requests
import google.auth
from flask import Flask, request
from googleapiclient import discovery
from googleapiclient.errors import HttpError

app = Flask(__name__)

def get_organization_id_for_project(project_id, crm_service):
    """
    Given a project_id, returns the organization ID by querying the project's ancestry.
    If no organization is found, returns None.
    """
    ancestry = crm_service.projects().getAncestry(projectId=project_id).execute()
    for ancestor in ancestry.get('ancestor', []):
        resource_id = ancestor.get('resourceId', {})
        # We want the node where resourceId.type == 'organization'
        if resource_id.get('type') == 'organization':
            return resource_id.get('id')
    return None

def update_firewall_for_all_projects(request):
    """
    Cloud Function entry point:
      1) Reads firewall configs from request payload.
      2) Uses project ancestry to find this project's parent organization.
      3) Fetches IPs for each config.
      4) Lists all projects in that organization.
      5) Creates or updates each firewall rule with the retrieved IPs.
    """
    # 1) Parse JSON from request
    data = request.get_json(silent=True)
    if not data:
        return "Invalid or missing JSON payload", 400

    # Expecting data to have: {"configs": [ {rule_name, description, endpoint_url}, ... ]}
    configs = data.get("configs")
    if not configs or not isinstance(configs, list):
        return "Missing or invalid 'configs' in request payload", 400

    # 2) Build the Resource Manager & Compute Engine clients
    # If running on GCF with default credentials, you can leave credentials=None
    credentials = None
    crm_service = discovery.build('cloudresourcemanager', 'v1', credentials=credentials)
    compute_service = discovery.build('compute', 'v1', credentials=credentials)

    # 3) Determine the project where this function runs & get its parent org
    _, current_project = google.auth.default()
    if not current_project:
        return "No GCP_PROJECT environment variable found. Are you running on Google Cloud Functions?", 400

    org_id = get_organization_id_for_project(current_project, crm_service)
    if not org_id:
        return f"No organization found for project: {current_project}", 400

    # 4) For each rule config, fetch IPs from the endpoint URLs.
    #    Store them in a dictionary for easy reference by rule_name.
    rule_ips_map = {}
    for cfg in configs:
        rule_name = cfg["rule_name"]

        # Check if endpoint_url exists, it takes precedence
        if "endpoint_url" in cfg:
            endpoint_url = cfg["endpoint_url"]
            print(f"Fetching IPs for rule '{rule_name}' from {endpoint_url}")

            resp = requests.get(endpoint_url)
            resp.raise_for_status()

            ip_list = resp.text.strip().split()
            ip_list = [ip.strip() for ip in ip_list if ip.strip()]
        # If no endpoint_url, use ip_list if provided
        elif "ip_list" in cfg:
            ip_list = cfg["ip_list"]
            print(f"Using provided IP list for rule '{rule_name}'")
        else:
            return f"Neither endpoint_url nor ip_list provided for rule '{rule_name}'", 400

        rule_ips_map[rule_name] = ip_list
        print(f"  -> Using {len(ip_list)} IP(s): {ip_list}")

    # 5) List all active projects in this organization
    filter_str = f"lifecycleState:ACTIVE parent.type:organization parent.id={org_id}"
    project_request = crm_service.projects().list(filter=filter_str)

    while project_request is not None:
        project_response = project_request.execute()
        projects = project_response.get('projects', [])

        for project in projects:
            project_id = project['projectId']
            project_name = project.get('name', project_id)
            print(f"\nChecking project: {project_name} [{project_id}]")

            # For each firewall rule config, attempt to get or create/update it
            for cfg in configs:
                rule_name = cfg["rule_name"]
                description = cfg["description"]
                new_ip_ranges = set(rule_ips_map[rule_name])  # from earlier

                # Attempt to get the firewall rule
                try:
                    fw_get_req = compute_service.firewalls().get(
                        project=project_id,
                        firewall=rule_name
                    )
                    current_fw = fw_get_req.execute()  # Will raise HttpError if not found

                    # Compare existing IPs to the new IPs
                    old_source_ranges = set(current_fw.get("sourceRanges", []))
                    if old_source_ranges == new_ip_ranges and current_fw.get("allowed") == cfg["allowed"]:
                        print(f"  [No Changes] Firewall rule '{rule_name}' has the same sourceRanges")
                    else:
                        print(f"  [Updating] Firewall rule '{rule_name}'...")
                        current_fw["sourceRanges"] = list(new_ip_ranges)
                        # Optionally keep the description in sync
                        current_fw["description"] = description

                        update_req = compute_service.firewalls().update(
                            project=project_id,
                            firewall=rule_name,
                            body=current_fw
                        )
                        update_resp = update_req.execute()
                        print(f"  [Success] Updated firewall rule '{rule_name}': {update_resp}")

                except HttpError as http_err:
                    # If status == 404, we create the rule
                    if http_err.resp.status == 404:
                        ensure_firewall_rule_exists(
                            project_id,
                            compute_service,
                            rule_name,
                            list(new_ip_ranges),
                            cfg["allowed"],
                            network='default',
                            description=description
                        )
                    else:
                        print(f"  HttpError for '{rule_name}' in {project_id}: {http_err}")
                except Exception as e:
                    print(f"  Unexpected error for '{rule_name}' in {project_id}: {e}")

        project_request = crm_service.projects().list_next(
            previous_request=project_request,
            previous_response=project_response
        )

    return "Done updating all projects."


def ensure_firewall_rule_exists(
    project_id,
    compute_service,
    rule_name,
    source_ranges,
    allowed,
    network='default',
    description='Allow SSH from specified IPs'
):
    """
    Creates a firewall rule with the specified rule_name, sourceRanges, and description,
    if it doesn't already exist (usually called after a 404).
    """
    print(f"  [Creating] Firewall rule '{rule_name}' in project {project_id}")
    firewall_body = {
        'name': rule_name,
        'description': description,
        'network': f'global/networks/{network}',
        'priority': 1000,
        'direction': 'INGRESS',
        'allowed': allowed,
        'sourceRanges': source_ranges
    }

    insert_req = compute_service.firewalls().insert(
        project=project_id,
        body=firewall_body
    )
    insert_resp = insert_req.execute()
    print(f"  [Success] Created firewall rule '{rule_name}': {insert_resp}")

@app.route("/", methods=["POST"])
def main_entry_point():
    result = update_firewall_for_all_projects(request)
    return result

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
