"""Route registration checks for the format and stories endpoints."""

from autoshorts.dashboard import app as app_mod


def test_story_request_model_defaults():
    req = app_mod.StoryJobRequest()
    assert req.mood is None and req.topic is None and req.provider is None


def test_routes_declared_in_source():
    import inspect

    src = inspect.getsource(app_mod)
    assert '"/api/stories"' in src
    assert '"/api/jobs/{job_id}/format"' in src
