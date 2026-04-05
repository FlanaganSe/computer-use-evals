"""Tests for the runtime result contract (Milestone 2)."""

from __future__ import annotations

from harness.runtime_results import (
    ExecutionMethod,
    ResultStatus,
    RuntimeResult,
    done,
    error,
    fail,
    ok,
)

# ---------------------------------------------------------------------------
# Summary string generation
# ---------------------------------------------------------------------------


class TestSummary:
    def test_ok_no_message(self) -> None:
        r = ok()
        assert r.summary == "ok"

    def test_ok_with_message(self) -> None:
        r = ok("downloaded:file.txt")
        assert r.summary == "ok:downloaded:file.txt"

    def test_error_with_message(self) -> None:
        r = error("click requires x,y coordinates")
        assert r.summary == "error:click requires x,y coordinates"

    def test_error_no_message(self) -> None:
        r = RuntimeResult(status=ResultStatus.ERROR)
        assert r.summary == "error"

    def test_done(self) -> None:
        r = done()
        assert r.summary == "done"

    def test_fail_with_reason(self) -> None:
        r = fail("Agent declared failure")
        assert r.summary == "fail:Agent declared failure"

    def test_fail_default_reason(self) -> None:
        r = fail()
        assert r.summary == "fail:Agent declared failure"

    def test_no_op_with_message(self) -> None:
        r = RuntimeResult(status=ResultStatus.NO_OP, message="element not found")
        assert r.summary == "no_op:element not found"

    def test_no_op_no_message(self) -> None:
        r = RuntimeResult(status=ResultStatus.NO_OP)
        assert r.summary == "no_op"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestToDict:
    def test_minimal(self) -> None:
        r = ok()
        d = r.to_dict()
        assert d["status"] == "ok"
        assert d["message"] == ""
        assert d["execution_method"] == "other"
        assert d["target_resolved"] is True
        assert "state_changed" not in d
        assert "expected_change_observed" not in d
        assert "metadata" not in d

    def test_with_all_fields(self) -> None:
        r = RuntimeResult(
            status=ResultStatus.OK,
            message="clicked Save",
            execution_method=ExecutionMethod.COORDINATES,
            target_resolved=True,
            state_changed=True,
            expected_change_observed=True,
            metadata={"element_id": "ax_abc123"},
        )
        d = r.to_dict()
        assert d["status"] == "ok"
        assert d["message"] == "clicked Save"
        assert d["execution_method"] == "coordinates"
        assert d["target_resolved"] is True
        assert d["state_changed"] is True
        assert d["expected_change_observed"] is True
        assert d["metadata"] == {"element_id": "ax_abc123"}

    def test_state_changed_none_excluded(self) -> None:
        r = ok()
        d = r.to_dict()
        assert "state_changed" not in d

    def test_state_changed_false_included(self) -> None:
        r = RuntimeResult(status=ResultStatus.OK, state_changed=False)
        d = r.to_dict()
        assert d["state_changed"] is False

    def test_empty_metadata_excluded(self) -> None:
        r = ok()
        assert "metadata" not in r.to_dict()


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------


class TestConstructors:
    def test_ok_defaults(self) -> None:
        r = ok()
        assert r.status == ResultStatus.OK
        assert r.message == ""
        assert r.execution_method == ExecutionMethod.OTHER
        assert r.target_resolved is True

    def test_ok_with_method(self) -> None:
        r = ok("pressed", method=ExecutionMethod.AX_PRESS)
        assert r.execution_method == ExecutionMethod.AX_PRESS

    def test_error_defaults(self) -> None:
        r = error("timeout")
        assert r.status == ResultStatus.ERROR
        assert r.message == "timeout"
        assert r.target_resolved is False

    def test_done_result(self) -> None:
        r = done()
        assert r.status == ResultStatus.DONE
        assert r.summary == "done"

    def test_fail_result(self) -> None:
        r = fail("no target")
        assert r.status == ResultStatus.FAIL
        assert r.message == "no target"

    def test_ok_with_metadata(self) -> None:
        r = ok("downloaded:file.txt", metadata={"filename": "file.txt"})
        assert r.metadata == {"filename": "file.txt"}
        assert r.summary == "ok:downloaded:file.txt"


# ---------------------------------------------------------------------------
# Equality
# ---------------------------------------------------------------------------


class TestEquality:
    def test_equal_results(self) -> None:
        a = ok("test")
        b = ok("test")
        assert a == b

    def test_different_status(self) -> None:
        a = ok()
        b = error("x")
        assert a != b

    def test_different_message(self) -> None:
        a = ok("a")
        b = ok("b")
        assert a != b

    def test_not_equal_to_non_result(self) -> None:
        r = ok()
        assert r != "ok"


# ---------------------------------------------------------------------------
# Repr
# ---------------------------------------------------------------------------


class TestRepr:
    def test_repr(self) -> None:
        r = ok("test")
        assert "RuntimeResult" in repr(r)
        assert "ok" in repr(r)
        assert "test" in repr(r)


# ---------------------------------------------------------------------------
# Summary backward compatibility with old string contract
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Summaries should match the patterns the old str results used."""

    def test_ok_starts_with_ok(self) -> None:
        assert ok().summary.startswith("ok")

    def test_ok_download_matches_old_format(self) -> None:
        r = ok("downloaded:file.txt")
        assert r.summary == "ok:downloaded:file.txt"

    def test_error_starts_with_error(self) -> None:
        assert error("timeout").summary.startswith("error:")

    def test_done_exact(self) -> None:
        assert done().summary == "done"

    def test_fail_starts_with_fail(self) -> None:
        assert fail("reason").summary.startswith("fail:")
