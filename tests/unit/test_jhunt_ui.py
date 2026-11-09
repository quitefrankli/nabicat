from web_app.users import User


def test_jhunt_is_admin_only_and_starts_disconnected(client, auth_loader):
    assert client.get('/jhunt-ui/').status_code == 302
    for admin in (False, True):
        user = User(username='viewer', password='pw', folder='viewer', is_admin=admin)
        auth_loader(user)
        with client.session_transaction() as session:
            session['_user_id'] = user.id
        home = client.get('/')
        response = client.get('/jhunt-ui/')
        if not admin:
            assert b'app-icon-jhunt' not in home.data
            assert response.status_code == 302
            continue
        assert b'/jhunt-ui/' in home.data
        assert response.status_code == 200
        for content in (b'Not connected', b'Inbox', b'Questions', b'Application review'):
            assert content in response.data
        assert b'url_for' not in response.data
        assert client.get('/jhunt-ui/static/app.js').status_code == 200
        assert client.get('/jhunt-ui/static/client.js').status_code == 200
        assert client.get('/jhunt-ui/static/style.css').status_code == 200
        assert b'/jhunt-ui/static/' in client.get('/service-worker.js').data
