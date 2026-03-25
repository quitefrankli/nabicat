import json
import pytest

from unittest.mock import patch, MagicMock, call
from email.mime.text import MIMEText


class TestSendAlertEmail:
    @patch('web_app.__main__.smtplib.SMTP')
    @patch('web_app.__main__.ConfigManager')
    def test_sends_email_with_correct_mime(self, mock_config, mock_smtp_class):
        mock_config.return_value.smtp_host = 'smtp.test.com'
        mock_config.return_value.smtp_port = 587
        mock_config.return_value.smtp_user = 'sender@test.com'
        mock_config.return_value.smtp_password = 'secret'
        mock_config.return_value.alert_email_to = 'alert@test.com'

        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        from web_app.__main__ import send_alert_email
        send_alert_email('Test Subject', 'Test body')

        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once_with('sender@test.com', 'secret')
        mock_smtp.sendmail.assert_called_once()

        args = mock_smtp.sendmail.call_args[0]
        assert args[0] == 'sender@test.com'
        assert args[1] == 'alert@test.com'
        assert 'Test Subject' in args[2]
        assert 'Test body' in args[2]


class TestCheckAndUpdateYtdlp:
    @patch('web_app.api.update_server')
    @patch('web_app.__main__.Repo')
    @patch('web_app.__main__.urllib.request.urlopen')
    def test_updates_and_pushes_when_new_version(self, mock_urlopen, mock_repo_cls, mock_update_server, tmp_path):
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("yt-dlp[default]>=2026.3.17\nother-pkg==1.0\n")

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"info": {"version": "2026.3.25"}}).encode()
        mock_urlopen.return_value = mock_resp

        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo

        from web_app.__main__ import _check_and_update_ytdlp
        with patch('web_app.__main__.Path') as mock_path:
            mock_path.return_value.resolve.return_value.parents.__getitem__ = lambda _, i: tmp_path
            _check_and_update_ytdlp()

        assert "yt-dlp[default]>=2026.3.25" in req_file.read_text()
        mock_repo.index.add.assert_called_once_with(["requirements.txt"])
        mock_repo.index.commit.assert_called_once_with("update yt-dlp to 2026.3.25")
        mock_repo.remotes.origin.push.assert_called_once()
        mock_update_server.assert_called_once()

    @patch('web_app.__main__.Repo')
    @patch('web_app.__main__.urllib.request.urlopen')
    def test_skips_update_when_version_current(self, mock_urlopen, mock_repo_cls, tmp_path):
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("yt-dlp[default]>=2026.3.17\n")

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"info": {"version": "2026.3.17"}}).encode()
        mock_urlopen.return_value = mock_resp

        from web_app.__main__ import _check_and_update_ytdlp
        with patch('web_app.__main__.Path') as mock_path:
            mock_path.return_value.resolve.return_value.parents.__getitem__ = lambda _, i: tmp_path
            _check_and_update_ytdlp()

        mock_repo_cls.assert_not_called()
        assert req_file.read_text() == "yt-dlp[default]>=2026.3.17\n"


class TestRunDownloadHealthCheck:
    @patch('web_app.__main__.send_alert_email')
    @patch('web_app.__main__.AudioDownloader')
    @patch('web_app.__main__.ConfigManager')
    def test_download_failure_sends_error_email(self, mock_config, mock_downloader, mock_send_email):
        mock_config.return_value.tubio_test_video_id = 'dQw4w9WgXcQ'
        mock_downloader._build_ydl_opts.return_value = {}
        mock_downloader.download_audio_file.side_effect = Exception('Download failed')

        from web_app.__main__ import run_download_health_check
        run_download_health_check()

        mock_send_email.assert_called_once()
        subject = mock_send_email.call_args[0][0]
        body = mock_send_email.call_args[0][1]
        assert 'FAIL' in subject.upper() or 'fail' in subject.lower()
        assert 'Download failed' in body
