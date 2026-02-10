from datetime import timedelta
from flask import Flask
from flask_bootstrap import Bootstrap5

from web_app.config import ConfigManager

app = Flask(__name__)

# Use a persistent secret key from environment variable
# Generate a random one if not set (for development only - will reset on restart)
app.secret_key = ConfigManager().flask_secret_key

# Session configuration for longer-lasting sessions
# 30 days session lifetime - especially helpful for mobile/iOS users
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=14)

# Cookie settings for better mobile browser compatibility
app.config['SESSION_COOKIE_SECURE'] = True  # Only send over HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent XSS access to session cookie
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection while allowing normal navigation

bootstrap = Bootstrap5(app)