from datetime import timedelta
from flask import Flask
from flask_bootstrap import Bootstrap5

app = Flask(__name__)

# Session configuration for longer-lasting sessions
# 30 days session lifetime - especially helpful for mobile/iOS users
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=14)

# Cookie settings for better mobile browser compatibility
app.config['SESSION_COOKIE_SECURE'] = True  # Only send over HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent XSS access to session cookie
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection while allowing normal navigation

bootstrap = Bootstrap5(app)