# OCI Terraform Setup for Lazy Wombat

This directory contains Terraform configurations for deploying the Lazy Wombat application on Oracle Cloud Infrastructure (OCI).

## Prerequisites

1. **OCI Account** - Sign up at https://www.oracle.com/cloud/free/
2. **Terraform** - Install from https://www.terraform.io/downloads
3. **OCI CLI** (optional but helpful) - Install from https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm

## OCI Credentials Setup

### Option 1: Using OCI CLI (Recommended)

Run `oci setup config` and follow the prompts to generate API keys and configuration.

### Option 2: Manual Setup

1. Generate an API key pair:
   ```bash
   mkdir -p ~/.oci
   openssl genrsa -out ~/.oci/oci_api_key.pem 2048
   chmod 600 ~/.oci/oci_api_key.pem
   openssl rsa -pubout -in ~/.oci/oci_api_key.pem -out ~/.oci/oci_api_key_public.pem
   ```

2. Get the key fingerprint:
   ```bash
   openssl rsa -pubout -outform DER -in ~/.oci/oci_api_key.pem | openssl md5 -c
   ```

3. Upload the public key in OCI Console:
   - Go to Profile (top right) → User Settings → API Keys → Add API Key
   - Paste the contents of `~/.oci/oci_api_key_public.pem`

4. Collect the required OCIDs:
   - **Tenancy OCID**: Profile → Tenancy: <your-tenancy> → Copy OCID
   - **User OCID**: Profile → User Settings → Copy OCID
   - **Compartment OCID**: Identity & Security → Compartments → Copy OCID of your compartment

## Configuration

1. Copy the example variables file:
   ```bash
   cd terraform/oci
   cp terraform.tfvars.example terraform.tfvars
   ```

2. Edit `terraform.tfvars` with your OCI credentials and preferences.

## Deployment

```bash
# Initialize Terraform
terraform init

# Review the plan
terraform plan

# Apply the configuration
terraform apply -auto-approve
```

After deployment, Terraform will output the public IP address:
```
elastic_ip = "xxx.xxx.xxx.xxx"
```

## Post-Deployment Setup

Use the same SSH-based setup as AWS (from the main README):

```bash
export ELASTIC_IP=$(terraform output elastic_ip | sed 's/"//g')

# Create user and setup SSH
ssh ubuntu@$ELASTIC_IP -t "sudo useradd -m $USER && sudo adduser $USER sudo && sudo cp -r ~/.ssh /home/$USER/ && sudo chown -R $USER:$USER /home/$USER && sudo chsh $USER -s /bin/bash && echo \"$USER ALL=(ALL) NOPASSWD: ALL\" | sudo tee -a /etc/sudoers"

# Copy SSH keys
scp ~/.ssh/id_rsa.pub $ELASTIC_IP:~/.ssh/
scp ~/.ssh/id_rsa $ELASTIC_IP:~/.ssh/

# Login and setup the application
ssh $USER@$ELASTIC_IP
git clone git@github.com:quitefrankli/lazywombat.git
cd lazywombat
bash lazywombat/setup_server.sh
```

## Destroy Resources

```bash
terraform destroy -auto-approve
```

## Differences from AWS Setup

| Feature | AWS | OCI |
|---------|-----|-----|
| Free Tier Instance | t2.micro (x86) | VM.Standard.A1.Flex (ARM) |
| Always Free Limits | 750 hrs/month | Always Free (no time limit) |
| Networking | VPC + Security Groups | VCN + Security Lists |
| Static IP | Elastic IP | Reserved Public IP |
| Budget | aws_budgets_budget | oci_budget_budget |

## Troubleshooting

- **API Authentication Errors**: Verify your API key fingerprint and OCIDs are correct
- **Quota Errors**: Check your service limits in OCI Console → Governance → Limits, Quotas and Usage
- **Shape Not Available**: Try a different availability domain or use a different shape
