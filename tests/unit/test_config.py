from web_app.config import ConfigManager


def test_app_config_sections_expose_grouped_values():
    cfg = ConfigManager()

    assert cfg.hammock.gallery_video_max_height_px == 720
    assert cfg.sentinel.screenshot_load_max_retries == 3
    assert cfg.tubio.max_search_pages == 3


def test_app_config_sections_are_mutable():
    cfg = ConfigManager()
    original = cfg.hammock.gallery_image_max_retries

    try:
        cfg.hammock.gallery_image_max_retries = 7

        assert cfg.hammock.gallery_image_max_retries == 7
    finally:
        cfg.hammock.gallery_image_max_retries = original
