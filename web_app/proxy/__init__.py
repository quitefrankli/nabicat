import requests
from flask import Blueprint, render_template, request, Response, abort
from flask_login import login_required, current_user


proxy_api = Blueprint(
    'proxy',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/proxy'
)


@proxy_api.before_request
@login_required
def before_request():
    # This ensures all routes in this blueprint require login and admin access
    if not current_user.is_admin:
        abort(403)


@proxy_api.context_processor
def inject_app_name():
    return dict(app_name='Proxy')


@proxy_api.route('/')
def index():
    return render_template("proxy_index.html")


@proxy_api.route('/browse', methods=['GET', 'POST'])
def browse():
    url = request.args.get('url') or request.form.get('url')
    if not url:
        return render_template("proxy_index.html", error="Please enter a URL")

    # Ensure URL has a scheme
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    try:
        # Make the request to the target URL
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

        resp = requests.get(url, headers=headers, timeout=10, allow_redirects=True)

        # Get content type
        content_type = resp.headers.get('Content-Type', 'text/html')

        # For HTML content, render in the proxy frame
        if 'text/html' in content_type:
            return render_template(
                "proxy_index.html",
                url=url,
                content=resp.text,
                status_code=resp.status_code
            )
        else:
            # For non-HTML content, pass through directly
            excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
            headers = [(name, value) for name, value in resp.raw.headers.items()
                      if name.lower() not in excluded_headers]
            return Response(resp.content, resp.status_code, headers)

    except requests.exceptions.Timeout:
        return render_template("proxy_index.html", url=url, error="Request timed out")
    except requests.exceptions.ConnectionError:
        return render_template("proxy_index.html", url=url, error="Could not connect to the URL")
    except requests.exceptions.InvalidURL:
        return render_template("proxy_index.html", url=url, error="Invalid URL format")
    except Exception as e:
        return render_template("proxy_index.html", url=url, error=f"Error: {str(e)}")
