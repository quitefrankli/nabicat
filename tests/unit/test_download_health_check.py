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
