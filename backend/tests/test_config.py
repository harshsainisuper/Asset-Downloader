from app.config import PROJECT_DIR, Settings


def test_env_example_loads():
    s = Settings(_env_file=PROJECT_DIR / ".env.example")
    assert s.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert s.temp_dir == (PROJECT_DIR / "backend" / "tmp").resolve()
    assert s.stt_provider == "none" and s.ffmpeg_path == "" and s.secret_key == ""


def test_cors_origins_formats():
    assert Settings(_env_file=None, cors_origins='["http://a"]').cors_origins == ["http://a"]
    assert Settings(_env_file=None, cors_origins="http://a, http://b").cors_origins == ["http://a", "http://b"]
