import logging
import math

import pytest
from PIL import Image

import main
from evo_vlac.utils.model_utils import GAC_model


class FakeCritic:
    def __init__(self):
        self.done_result = [0.91]
        self.done_results = None
        self.critic_result = ([46.0], [0.0, 46.0])
        self.done_call = None
        self.done_calls = []
        self.critic_call = None

    def get_trajectory_done(self, **kwargs):
        self.done_call = kwargs
        self.done_calls.append(kwargs)
        if self.done_results is not None:
            return self.done_results.pop(0)
        return self.done_result

    def get_trajectory_critic(self, **kwargs):
        self.critic_call = kwargs
        return self.critic_result


def _setup(monkeypatch):
    fake = FakeCritic()
    images = {
        "current": Image.new("RGB", (2, 3), "blue"),
        "reference": Image.new("RGB", (4, 5), "green"),
        "before": Image.new("RGB", (6, 7), "red"),
        "after": Image.new("RGB", (8, 9), "white"),
    }
    monkeypatch.setattr(main, "CRITIC", fake)
    monkeypatch.setattr(main, "_normalize_image_input", lambda value: images.get(value, images["current"]))
    return fake, images


def _navigation(**overrides):
    payload = {
        "current_image": "current",
        "reference_image": "reference",
        "done_threshold": 0.8,
        "rich": True,
    }
    payload.update(overrides)
    return main.NavigationVerifyRequest(**payload)


def _grasp(**overrides):
    payload = {
        "before_image": "before",
        "after_image": "after",
        "target_label": "bottle",
        "done_threshold": 0.35,
        "rich": False,
    }
    payload.update(overrides)
    return main.GraspVerifyRequest(**payload)


def test_routes_are_registered():
    paths = {route.path for route in main.app.routes}
    assert {"/health", "/critic", "/navigation/verify", "/grasp/verify"} <= paths


def test_navigation_calls_done_with_goal_image(monkeypatch):
    fake, images = _setup(monkeypatch)
    response = main.navigation_verify(_navigation())

    assert fake.done_call["goal_image"] is images["reference"]
    assert fake.done_call["image_list"] == [images["current"]]
    assert fake.done_call["ref_image_list"] is None
    assert fake.done_call["batch_num"] == 1
    assert fake.done_call["threshold"] == 0.0
    assert fake.done_call["skip"] == 1
    assert response.visual_done is True


def test_navigation_low_score_is_normal_visual_failure(monkeypatch):
    fake, _ = _setup(monkeypatch)
    fake.done_result = [0.3]
    response = main.navigation_verify(_navigation())
    assert response.visual_done is False
    assert response.score_in_expected_range is True


def test_navigation_empty_result_is_protocol_error(monkeypatch):
    fake, _ = _setup(monkeypatch)
    fake.done_result = []
    with pytest.raises(main.ServiceError, match="cannot be empty") as exc:
        main.navigation_verify(_navigation())
    assert exc.value.status_code == 502
    assert exc.value.error_type == "PROTOCOL_ERROR"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_navigation_non_finite_results_are_protocol_errors(monkeypatch, value):
    fake, _ = _setup(monkeypatch)
    fake.done_result = [value]
    with pytest.raises(main.ServiceError) as exc:
        main.navigation_verify(_navigation())
    assert exc.value.status_code == 502
    assert exc.value.error_type == "PROTOCOL_ERROR"


def test_navigation_out_of_range_forces_false_and_warning(monkeypatch):
    fake, _ = _setup(monkeypatch)
    fake.done_result = [1.2]
    response = main.navigation_verify(_navigation())
    assert response.done_score == 1.2
    assert response.score_in_expected_range is False
    assert response.visual_done is False
    assert response.warning


def test_navigation_rejects_invalid_threshold(monkeypatch):
    _setup(monkeypatch)
    with pytest.raises(main.ServiceError) as exc:
        main.navigation_verify(_navigation(done_threshold=1.1))
    assert exc.value.status_code == 400
    assert exc.value.error_type == "INPUT_ERROR"


def test_grasp_route_runs_presence_and_visibility_on_after_only(monkeypatch):
    fake, images = _setup(monkeypatch)
    fake.done_results = [[0.0], [1.0]]
    response = main.grasp_verify(_grasp())

    assert len(fake.done_calls) == 2
    presence_call, visibility_call = fake.done_calls
    assert presence_call["task"] == "当前最终图像中，bottle 是否仍然位于桌面上？"
    assert visibility_call["task"] == main.GRASP_VISIBILITY_TASK
    for call in fake.done_calls:
        assert call["image_list"] == [images["after"]]
        assert images["before"] not in call["image_list"]
        assert call["goal_image"] is None
        assert call["ref_image_list"] is None
        assert call["batch_num"] == 1
        assert call["threshold"] == 0.0
        assert call["apply_threshold"] is False
        assert call["skip"] == 1
    assert fake.critic_call is None
    assert response.mode == "grasp_removal"
    assert response.method == "trajectory_done_presence"
    assert response.target_present_score == 0.0
    assert response.table_visible_score == 1.0
    assert response.decision == "REMOVED"
    assert response.removal_confirmed is True
    assert response.evidence_status == "REMOVAL_CONFIRMED"


@pytest.mark.parametrize("target_label", ["bottle", "cup", "apple", "book"])
def test_grasp_presence_prompt_uses_dynamic_target_label(monkeypatch, target_label):
    fake, _ = _setup(monkeypatch)
    fake.done_results = [[1.0], [1.0]]
    main.grasp_verify(_grasp(target_label=target_label))
    assert fake.done_calls[0]["task"] == f"当前最终图像中，{target_label} 是否仍然位于桌面上？"


def test_grasp_uses_fixed_queries_even_when_legacy_task_is_supplied(monkeypatch):
    fake, _ = _setup(monkeypatch)
    fake.done_results = [[0.0], [1.0]]
    main.grasp_verify(_grasp(task_description=" legacy task "))
    assert fake.done_calls[0]["task"] == "当前最终图像中，bottle 是否仍然位于桌面上？"
    assert fake.done_calls[1]["task"] == main.GRASP_VISIBILITY_TASK


@pytest.mark.parametrize(
    (
        "presence_score",
        "visibility_score",
        "expected_decision",
        "expected_confirmed",
        "expected_status",
    ),
    [
        (1.0, 1.0, "STILL_PRESENT", False, "TARGET_STILL_PRESENT"),
        (0.35, 1.0, "STILL_PRESENT", False, "TARGET_STILL_PRESENT"),
        (0.0, 1.0, "REMOVED", True, "REMOVAL_CONFIRMED"),
        (0.0, 0.35, "REMOVED", True, "REMOVAL_CONFIRMED"),
        (0.0, 0.0, "UNCERTAIN", False, "VISUAL_EVIDENCE_UNCERTAIN"),
    ],
)
def test_grasp_three_state_semantics(
    monkeypatch,
    presence_score,
    visibility_score,
    expected_decision,
    expected_confirmed,
    expected_status,
):
    fake, _ = _setup(monkeypatch)
    fake.done_results = [[presence_score], [visibility_score]]
    response = main.grasp_verify(_grasp())
    assert response.presence_threshold == 0.35
    assert response.visibility_threshold == 0.35
    assert response.decision == expected_decision
    assert response.removal_confirmed is expected_confirmed
    assert response.evidence_status == expected_status


@pytest.mark.parametrize("value", [-0.1, 1.2, float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("query", ["presence", "visibility"])
def test_grasp_invalid_model_scores_are_protocol_errors(monkeypatch, value, query):
    fake, _ = _setup(monkeypatch)
    fake.done_results = [[value], [1.0]] if query == "presence" else [[0.0], [value]]
    with pytest.raises(main.ServiceError) as exc:
        main.grasp_verify(_grasp())
    assert exc.value.status_code == 502
    assert exc.value.error_type == "PROTOCOL_ERROR"


@pytest.mark.parametrize("result", [[], [0.0, 0.5], [0.0, 0.5, 0.7]])
@pytest.mark.parametrize("query", ["presence", "visibility"])
def test_grasp_wrong_result_count_is_protocol_error(monkeypatch, result, query):
    fake, _ = _setup(monkeypatch)
    fake.done_results = [result, [1.0]] if query == "presence" else [[0.0], result]
    with pytest.raises(main.ServiceError) as exc:
        main.grasp_verify(_grasp())
    assert exc.value.status_code == 502
    assert exc.value.error_type == "PROTOCOL_ERROR"


def test_grasp_consistency_contradictions_are_protocol_errors():
    common = {
        "target_present_score": 0.0,
        "presence_threshold": 0.35,
        "table_visible_score": 1.0,
        "visibility_threshold": 0.35,
        "decision": "REMOVED",
        "removal_confirmed": True,
        "evidence_status": "REMOVAL_CONFIRMED",
    }
    contradictions = [
        {"decision": "UNCERTAIN"},
        {"removal_confirmed": False},
        {"evidence_status": "VISUAL_EVIDENCE_UNCERTAIN"},
    ]
    for override in contradictions:
        values = {**common, **override}
        with pytest.raises(main.ServiceError) as exc:
            main._validate_grasp_result_consistency(**values)
        assert exc.value.status_code == 502
        assert exc.value.error_type == "PROTOCOL_ERROR"


class DoneInferenceProbe:
    def __init__(self, value):
        self.value = value

    def get_done_prompt(self, task):
        return task

    def get_infer_requests(self, prompt, images):
        return {"prompt": prompt, "images": images}

    def chat(self, infer_requests):
        return [], 0.0

    def results_format(self, response_list, infer_requests, rich=False):
        return [self.value], []


def _call_model_done(value, *, threshold=0.0, apply_threshold=True):
    probe = DoneInferenceProbe(value)
    return GAC_model.get_trajectory_done(
        probe,
        task="test",
        image_list=[object()],
        batch_num=1,
        threshold=threshold,
        apply_threshold=apply_threshold,
    )


def test_model_done_default_still_applies_threshold():
    assert _call_model_done(0.3, threshold=0.35) == [0.0]
    assert _call_model_done(0.5, threshold=0.35) == [0.5]


def test_model_done_raw_preserves_positive_and_negative_values():
    assert _call_model_done(0.25, apply_threshold=False) == [0.25]
    assert _call_model_done(-0.25, apply_threshold=False) == [-0.25]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_model_done_raw_preserves_non_finite_values(value):
    result = _call_model_done(value, apply_threshold=False)[0]
    if math.isnan(value):
        assert math.isnan(result)
    else:
        assert result == value


def test_model_unavailable_is_not_reported_as_visual_failure(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setattr(main, "CRITIC", None)
    for invoke in (lambda: main.navigation_verify(_navigation()), lambda: main.grasp_verify(_grasp())):
        with pytest.raises(main.ServiceError) as exc:
            invoke()
        assert exc.value.status_code == 503
        assert exc.value.error_type == "MODEL_UNAVAILABLE"


def test_existing_critic_api_remains_compatible(monkeypatch):
    fake, images = _setup(monkeypatch)
    fake.critic_result = ([-12.0], [0.0, -12.0])
    response = main.critic(
        main.CriticRequest(
            image="current",
            reference_image="reference",
            task_description="test task",
            batch_num=1,
            rich=False,
        )
    )
    assert response.critic_list == [-12.0]
    assert response.value_list == [0.0, -12.0]
    assert fake.critic_call["image_list"] == [images["reference"], images["current"]]


def test_logs_do_not_include_full_base64(monkeypatch, caplog):
    _setup(monkeypatch)
    secret_payload = "A" * 4096
    caplog.set_level(logging.INFO, logger="vlac.service")
    main.navigation_verify(_navigation(current_image=secret_payload, reference_image=secret_payload))
    assert secret_payload not in caplog.text


def test_web_trajectory_done_forwards_reference_as_goal_image():
    class WrapperProbe:
        call = None

        def get_trajectory_done(self, **kwargs):
            self.call = kwargs
            return [0.75]

    probe = WrapperProbe()
    result = GAC_model.web_trajectory_done(probe, "task", "current.jpg", "reference.jpg", rich=True)
    assert probe.call["image_list"] == ["current.jpg"]
    assert probe.call["goal_image"] == "reference.jpg"
    assert probe.call["ref_image_list"] is None
    assert "0.75" in result
