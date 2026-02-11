# Web App


## Setup

### Env

create a `.env` file in the root of the project with the following content:

* `X_RAPID_API_KEY` - from https://rapidapi.com/hub
* `FLASK_SECRET_KEY` - can be any random 24 char str

### Conda + Other Misc Reqs

* setup conda env - see `setup_server.sh:setup_conda`
* install `ffmpeg`


## Running

```bash
pip install -r requirements.txt
python -m web_app [--debug] [--port PORT]
```

## Testing

`python -m pytest`

### Playwright UI Tests

need an initial setup

```bash
pip install playwright
playwright install
sudo $(which playwright) install-deps
```

run ui tests in headless mode

`pytest tests/ui/`

to see the UI in the test

`pytest tests/ui/ --headed --slowmo 500`


## Cloud Setup

### AWS (Default)

1. install terraform
2. `terraform -chdir=terraform/aws init`
3. `terraform -chdir=terraform/aws plan`
4. `terraform -chdir=terraform/aws apply -auto-approve`

terraform should output something like

> elastic_ip = "12.12.123.123"

with the generated ip, the "A Record" would need to be updated via the appropriate domain provider

### Oracle Cloud Infrastructure (OCI)

An equivalent OCI setup is available in `terraform/oci/` directory.

```bash
cd terraform/oci
terraform init
terraform plan
terraform apply -auto-approve
```

See `terraform/oci/README.md` for detailed OCI-specific setup instructions.

### Setup EC2

```bash
export ELASTIC_IP=$(terraform -chdir=terraform/aws output elastic_ip | sed 's/\"//g')

ssh ubuntu@$ELASTIC_IP -t "sudo useradd -m $USER && sudo adduser $USER sudo && sudo cp -r ~/.ssh /home/$USER/ && sudo chown -R $USER:$USER /home/$USER && sudo chsh $USER -s /bin/bash && echo \"$USER ALL=(ALL) NOPASSWD: ALL\" | sudo tee -a /etc/sudoers"

# assuming ssh key has been added to GitHub already
scp ~/.ssh/id_rsa.pub $ELASTIC_IP:~/.ssh/
scp ~/.ssh/id_rsa $ELASTIC_IP:~/.ssh/
```

```bash
ssh $ELASTIC_IP
git clone git@github.com:quitefrankli/lazywombat.git
cd lazywombat
bash lazywombat/setup_server.sh
```

## Updating Server

a. simply push from main branch, force push also works too
b. on main branch run - `python scripts/api_helper.py update`
c. run on server - `bash update_server.sh &> logs/shell_logs.log &`

## Renewing Cert

```bash
sudo systemctl stop nginx 
sudo $(which certbot) renew
sudo systemctl start nginx 
```