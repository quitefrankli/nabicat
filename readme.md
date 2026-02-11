# Web App


## Setup

### Env

create a `.env` file in the root of the project with the following content:

* `X_RAPID_API_KEY` - from https://rapidapi.com/hub
* `FLASK_SECRET_KEY` - can be any random 24 char str

### Conda + Other Misc Reqs

* setup conda env - see `setup_server.sh:setup_conda`
* install `ffmpeg`
* install `terraform`

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

first register host .ssh creds as github ssh key

then run client side setup

`source setup_server.sh && run_client_side $CLOUD_PROVIDER`

CLOUD_PROVIDER is either aws or oci

to ssh onto the server use the below ip address

`export SERVER_IP_ADDR=$(terraform -chdir=terraform/$CLOUD_PROVIDER output server_ip_addr | sed 's/\"//g')`

to bring down the server

`terraform -chdir=terraform/$CLOUD_PROVIDER destroy`

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