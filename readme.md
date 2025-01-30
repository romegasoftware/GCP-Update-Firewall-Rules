# GCP: Update Firewall Rules Across Multiple Projects

This Cloud Run will update firewall rules across multiple projects in your organization. This project is meant to be deployed and to manage Google Cloud and manage firewall rules for 3rd party services that publish their IP whitelisting requirements as an endpoint with each IP on a new line ([for example](https://envoyer.io/ips-v4.txt)). It will run on a configurable cron schedule to keep your firewall rules up to date.

Once deployed as a Cloud Run, it can receive a JSON payload with the following structure:

```json
{
    "configs": [
        {
            "rule_name": "allow-ssh-laravel-forge",
            "description": "Allow SSH from Laravel Forge IPs",
            "endpoint_url": "https://forge.laravel.com/ips-v4.txt",
            "allowed": [
                {
                    "IPProtocol": "tcp",
                    "ports": [
                        "22"
                    ]
                }
            ]
        },
        {
            "rule_name": "allow-ssh-laravel-envoyer",
            "description": "Allow SSH from Laravel Envoyer IPs",
            "endpoint_url": "https://envoyer.io/ips-v4.txt",
            "allowed": [
                {
                    "IPProtocol": "tcp",
                    "ports": [
                        "22"
                    ]
                }
            ]
        }
    ]
}
```

- `rule_name` is the name of the firewall rule you want to create or update.
- `description` is the description of the firewall rule you want to update.
- `endpoint_url` is the endpoint URL that will provide the IP addresses to allow.
- `allowed` is the list of allowed protocols and ports.

If it does not find an existing firewall that matches the `rule_name`, it will create one.

If it finds an existing rule with the same `rule_name`, it will check to ensure its `allowed` definition still matches and that the IP endpoints are the same. If not, it will update the firewall rule.

# Setup

## Create Service Account

```bash
gcloud iam service-accounts create cr-firewall-update \
  --display-name="Cloud Run Firewall Update"
```

This will create a service account with the name `cr-firewall-update`. Use this email in the following steps for `SERVICE_ACCOUNT_EMAIL`.

## Add IAM Policy Binding

This should be at the **organization level**. Use your [organization ID](https://cloud.google.com/resource-manager/docs/creating-managing-organization#retrieving_your_organization_id), not the project ID.

```bash
gcloud organizations add-iam-policy-binding {ORGANIZATION_ID} \
  --member="serviceAccount:{SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/compute.securityAdmin"
gcloud organizations add-iam-policy-binding {ORGANIZATION_ID} \
  --member="serviceAccount:{SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/compute.securityAdmin"
gcloud organizations add-iam-policy-binding {ORGANIZATION_ID} \
  --member="serviceAccount:{SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/resourcemanager.organizationViewer"
```

## Add IAM Policy Binding for Cloud Run Invoker

You may add this permission to the project level instead if you wish to limit the scope of access. Alternatively, you can still utilize the `ORGANIZATION_ID` instead of a `PROJECT_ID`. The `PROJECT_ID` is the ID of the project to which the Cloud Run Function will be deployed in the next step.

```bash
gcloud projects add-iam-policy-binding {PROJECT_ID} \
  --member="serviceAccount:{SERVICE_ACCOUNT_EMAIL}" \
  --role="roles/run.invoker"
```

## Setup Cloud Run Function

This step will create the Cloud Run Function and use the source code from the current directory as its source. You can go ahead and skip this step if you'd like to manually add the function to the project.

```bash
gcloud run deploy cr-firewall-update \
  --source=. \
  --region=us-east1 \
  --service-account={SERVICE_ACCOUNT_EMAIL} \
  --no-allow-unauthenticated \
  --ingress=internal
```

This will output the URL of the Cloud Run Function Service URL. Use this in the next step for `CLOUD_RUN_URL`.

### Manually Adding The Cloud Run

These are the steps to manually add the function to the project if you choose not to do the automated setup in the previous step:

- Setup a Python 3.12 runtime.
- Add the contents of `main.py` to the function.
- Add the contents of `requirements.txt` to the function.
- Deploy the function.

## Setup Cloud Scheduler

Run this command to create a Cloud Scheduler job that will run the function at 12:00 AM EST on the first day of every month. Adjust the cron schedule as needed. It will configure the request's body to be the contents of `payload.json`. Please update that payload as needed before running the command.

```bash
gcloud scheduler jobs create http cr-firewall-update \
  --schedule="0 0 1 * *" \
  --uri={CLOUD_RUN_URL} \
  --oidc-service-account-email={SERVICE_ACCOUNT_EMAIL} \
  --location=us-east1 \
  --http-method=POST \
  --message-body-from-file=payload.json \
  --headers Content-Type=application/json
```
