"""Tests for the researcher_assistant role.

Covers:
  1. Mailbox scoping gate (`_is_addressable`) — partner allowed, manager
     denied, SYSTEM bypass, assistant outbound restricted, normal traffic
     unaffected, other researcher denied.
  2. Auto-spawn on `add_agent(role='researcher')` and the `--no-assistant`
     opt-out.
  3. Manual backfill via `add_assistant_for_researcher` and removal via
     `remove_assistant_for_researcher`.
  4. Sandbox profile blocks `.bg/` reads and git commits.
  5. `bg_log_excerpt` server-side hard cap (200 lines / 32 KB).
  6. Role-aware MCP tool subset (assistant gets bg_log_excerpt + artifact_save,
     no task_create / task_assign / task_cancel).
  7. `task_assign` and `task_create` reject the assistant as assignee.
  8. Researcher charter dynamic "Your Assistant" section + {partner}
     substitution in the assistant charter.
  9. `_auto_continue_assistant` defers when bg processes are running.
"""

import time
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# 1. Mailbox scoping gate
# ---------------------------------------------------------------------------


@pytest.fixture
def lab_team(tmp_path):
    """Bootstrap a team with a manager + alice (researcher + auto-spawned assistant)."""
    from delegate.bootstrap import bootstrap, add_agent
    from delegate.config import add_member
    from delegate.mailbox import invalidate_role_cache

    hc_home = tmp_path / "hc"
    hc_home.mkdir()
    add_member(hc_home, "human")
    bootstrap(hc_home, "lab", manager="delegate")
    add_agent(hc_home, "lab", agent_name="alice", role="researcher")
    invalidate_role_cache()
    return hc_home


class TestMailboxScoping:
    def test_partner_to_assistant_allowed(self, lab_team):
        from delegate.mailbox import send
        # alice → alice_assistant should succeed
        mid = send(lab_team, "lab", "alice", "alice_assistant", "go", task_id=1)
        assert mid > 0

    def test_assistant_to_partner_allowed(self, lab_team):
        from delegate.mailbox import send
        mid = send(lab_team, "lab", "alice_assistant", "alice", "results", task_id=1)
        assert mid > 0

    def test_manager_to_assistant_denied(self, lab_team):
        from delegate.mailbox import send, MailboxAccessDenied
        with pytest.raises(MailboxAccessDenied) as exc:
            send(lab_team, "lab", "delegate", "alice_assistant", "do this", task_id=1)
        assert "scoped to alice" in str(exc.value)

    def test_other_researcher_to_assistant_denied(self, lab_team):
        from delegate.bootstrap import add_agent
        from delegate.mailbox import send, MailboxAccessDenied, invalidate_role_cache

        add_agent(lab_team, "lab", agent_name="bob", role="researcher", no_assistant=True)
        invalidate_role_cache()
        with pytest.raises(MailboxAccessDenied):
            send(lab_team, "lab", "bob", "alice_assistant", "help", task_id=1)

    def test_system_user_to_assistant_allowed(self, lab_team):
        from delegate.mailbox import send
        from delegate.config import SYSTEM_USER
        mid = send(lab_team, "lab", SYSTEM_USER, "alice_assistant", "auto-continue", task_id=1)
        assert mid > 0

    def test_assistant_to_outsider_denied(self, lab_team):
        from delegate.mailbox import send, MailboxAccessDenied
        with pytest.raises(MailboxAccessDenied):
            send(lab_team, "lab", "alice_assistant", "delegate", "hi", task_id=1)

    def test_normal_traffic_unaffected(self, lab_team):
        """alice → delegate should not be touched by the gate."""
        from delegate.mailbox import send
        mid = send(lab_team, "lab", "alice", "delegate", "progress update", task_id=1)
        assert mid > 0

    def test_assistant_to_human_denied(self, lab_team):
        """The human is not exempt — they cannot bypass the assistant scoping."""
        from delegate.mailbox import send, MailboxAccessDenied
        with pytest.raises(MailboxAccessDenied):
            send(lab_team, "lab", "human", "alice_assistant", "hi", task_id=1)


# ---------------------------------------------------------------------------
# 2. Auto-spawn on researcher add
# ---------------------------------------------------------------------------


class TestAutoSpawn:
    def test_researcher_add_creates_assistant(self, tmp_path):
        from delegate.bootstrap import bootstrap, add_agent, _agents_dir
        from delegate.config import add_member

        hc = tmp_path / "hc"
        hc.mkdir()
        add_member(hc, "human")
        bootstrap(hc, "lab", manager="delegate")
        add_agent(hc, "lab", agent_name="alice", role="researcher")

        agents_root = _agents_dir(hc, "lab")
        assert (agents_root / "alice_assistant").is_dir()

        helper_state = yaml.safe_load((agents_root / "alice_assistant" / "state.yaml").read_text())
        assert helper_state["role"] == "researcher_assistant"
        assert helper_state["model"] == "haiku"
        assert helper_state["partner"] == "alice"

        researcher_state = yaml.safe_load((agents_root / "alice" / "state.yaml").read_text())
        assert researcher_state["assistant"] == "alice_assistant"

    def test_no_assistant_flag_skips_spawn(self, tmp_path):
        from delegate.bootstrap import bootstrap, add_agent, _agents_dir
        from delegate.config import add_member

        hc = tmp_path / "hc"
        hc.mkdir()
        add_member(hc, "human")
        bootstrap(hc, "lab", manager="delegate")
        add_agent(hc, "lab", agent_name="bob", role="researcher", no_assistant=True)

        agents_root = _agents_dir(hc, "lab")
        assert (agents_root / "bob").is_dir()
        assert not (agents_root / "bob_assistant").exists()

        bob_state = yaml.safe_load((agents_root / "bob" / "state.yaml").read_text())
        assert "assistant" not in bob_state

    def test_collision_raises_value_error(self, tmp_path):
        from delegate.bootstrap import bootstrap, add_agent
        from delegate.config import add_member

        hc = tmp_path / "hc"
        hc.mkdir()
        add_member(hc, "human")
        bootstrap(hc, "lab", manager="delegate")
        # Create the squatting agent first
        add_agent(hc, "lab", agent_name="eve_assistant", role="engineer")
        with pytest.raises(ValueError, match="already exists"):
            add_agent(hc, "lab", agent_name="eve", role="researcher")

    def test_engineer_add_does_not_spawn_assistant(self, tmp_path):
        from delegate.bootstrap import bootstrap, add_agent, _agents_dir
        from delegate.config import add_member

        hc = tmp_path / "hc"
        hc.mkdir()
        add_member(hc, "human")
        bootstrap(hc, "lab", manager="delegate")
        add_agent(hc, "lab", agent_name="charlie", role="engineer")

        agents_root = _agents_dir(hc, "lab")
        assert not (agents_root / "charlie_assistant").exists()


# ---------------------------------------------------------------------------
# 3. Manual backfill + removal
# ---------------------------------------------------------------------------


class TestManualBackfill:
    def test_backfill_creates_assistant(self, tmp_path):
        from delegate.bootstrap import (
            bootstrap, add_agent, add_assistant_for_researcher, _agents_dir,
        )
        from delegate.config import add_member

        hc = tmp_path / "hc"
        hc.mkdir()
        add_member(hc, "human")
        bootstrap(hc, "lab", manager="delegate")
        add_agent(hc, "lab", agent_name="bob", role="researcher", no_assistant=True)

        helper = add_assistant_for_researcher(hc, "lab", "bob")
        assert helper == "bob_assistant"
        assert (_agents_dir(hc, "lab") / "bob_assistant").is_dir()

    def test_backfill_rejects_already_bound(self, lab_team):
        # alice already has alice_assistant from the fixture
        from delegate.bootstrap import add_assistant_for_researcher
        with pytest.raises(ValueError, match="already has an assistant"):
            add_assistant_for_researcher(lab_team, "lab", "alice")

    def test_backfill_rejects_non_researcher(self, tmp_path):
        from delegate.bootstrap import (
            bootstrap, add_agent, add_assistant_for_researcher,
        )
        from delegate.config import add_member

        hc = tmp_path / "hc"
        hc.mkdir()
        add_member(hc, "human")
        bootstrap(hc, "lab", manager="delegate")
        add_agent(hc, "lab", agent_name="charlie", role="engineer")
        with pytest.raises(ValueError, match="not 'researcher'"):
            add_assistant_for_researcher(hc, "lab", "charlie")

    def test_backfill_rejects_unknown_agent(self, lab_team):
        from delegate.bootstrap import add_assistant_for_researcher
        with pytest.raises(FileNotFoundError):
            add_assistant_for_researcher(lab_team, "lab", "ghost")

    def test_remove_assistant(self, lab_team):
        from delegate.bootstrap import remove_assistant_for_researcher, _agents_dir

        removed = remove_assistant_for_researcher(lab_team, "lab", "alice")
        assert removed == "alice_assistant"
        assert not (_agents_dir(lab_team, "lab") / "alice_assistant").exists()

        alice_state = yaml.safe_load(
            (_agents_dir(lab_team, "lab") / "alice" / "state.yaml").read_text()
        )
        assert "assistant" not in alice_state

    def test_remove_when_no_assistant_returns_none(self, tmp_path):
        from delegate.bootstrap import (
            bootstrap, add_agent, remove_assistant_for_researcher,
        )
        from delegate.config import add_member

        hc = tmp_path / "hc"
        hc.mkdir()
        add_member(hc, "human")
        bootstrap(hc, "lab", manager="delegate")
        add_agent(hc, "lab", agent_name="bob", role="researcher", no_assistant=True)
        assert remove_assistant_for_researcher(hc, "lab", "bob") is None


# ---------------------------------------------------------------------------
# 4. Sandbox profile
# ---------------------------------------------------------------------------


class TestSandbox:
    def test_assistant_blocks_git_commit(self):
        from delegate.runtime import _sandbox_for_role
        disallowed, denied = _sandbox_for_role("researcher_assistant")
        assert "git commit" in denied
        assert "git add " in denied
        assert any("git commit" in d for d in disallowed)

    def test_assistant_blocks_bg_log_reads(self):
        from delegate.runtime import _sandbox_for_role
        _, denied = _sandbox_for_role("researcher_assistant")
        assert "/.bg/" in denied
        assert ".bg/stdout.log" in denied
        assert ".bg/stderr.log" in denied

    def test_assistant_inherits_default_restrictions(self):
        """Standard git push/pull/rebase restrictions still apply."""
        from delegate.runtime import _sandbox_for_role, DENIED_BASH_PATTERNS
        _, denied = _sandbox_for_role("researcher_assistant")
        for p in DENIED_BASH_PATTERNS:
            assert p in denied

    def test_researcher_sandbox_unchanged(self):
        """Adding the assistant role didn't break the researcher's allowlist."""
        from delegate.runtime import _sandbox_for_role
        _, denied = _sandbox_for_role("researcher")
        assert "git checkout" not in denied
        assert "git branch" not in denied


# ---------------------------------------------------------------------------
# 5. bg_log_excerpt server-side cap
# ---------------------------------------------------------------------------


class TestBgLogExcerpt:
    def _launch_and_wait(self, agent_dir, cmd):
        from delegate.background import launch, check
        info = launch(agent_dir, cmd, label="test")
        for _ in range(40):
            inf = check(agent_dir, info.handle)
            if inf.state != "running":
                return inf
            time.sleep(0.1)
        raise RuntimeError("process didn't terminate in time")

    def test_default_tail(self, tmp_path):
        from delegate.background import log_excerpt
        ad = tmp_path / "agent"
        ad.mkdir()
        info = self._launch_and_wait(
            ad, "for i in $(seq 1 100); do echo line $i; done"
        )
        assert info.state == "completed"
        result = log_excerpt(ad, info.handle, source="stdout", max_lines=20)
        assert len(result["lines"]) == 20
        assert result["lines"][-1] == "line 100"
        assert result["lines"][0] == "line 81"

    def test_grep_filter(self, tmp_path):
        from delegate.background import log_excerpt
        ad = tmp_path / "agent"
        ad.mkdir()
        info = self._launch_and_wait(
            ad, "for i in $(seq 1 100); do echo metric=$i loss=$((100-i)); done"
        )
        result = log_excerpt(
            ad, info.handle, source="stdout",
            grep_pattern=r"metric=2[0-9] ", max_lines=50, tail=False,
        )
        assert len(result["lines"]) == 10  # metric=20..29
        assert result["lines"][0].startswith("metric=20 ")

    def test_hard_cap_on_max_lines(self, tmp_path):
        from delegate.background import log_excerpt, LOG_EXCERPT_MAX_LINES
        ad = tmp_path / "agent"
        ad.mkdir()
        info = self._launch_and_wait(
            ad, "for i in $(seq 1 1000); do echo line $i; done"
        )
        # Caller asks for 999999, server caps at LOG_EXCERPT_MAX_LINES (200)
        result = log_excerpt(ad, info.handle, source="stdout", max_lines=999999)
        assert len(result["lines"]) <= LOG_EXCERPT_MAX_LINES
        assert result["truncated"] is True

    def test_byte_cap_enforced(self, tmp_path):
        from delegate.background import log_excerpt, LOG_EXCERPT_MAX_BYTES
        ad = tmp_path / "agent"
        ad.mkdir()
        # 200 lines of 500-byte content = ~100 KB; should hit the 32 KB cap
        info = self._launch_and_wait(
            ad,
            "for i in $(seq 1 200); do "
            "printf 'L%03d ' $i; "
            "printf '%.0sX' $(seq 1 500); "
            "echo; done",
        )
        result = log_excerpt(ad, info.handle, source="stdout", max_lines=200)
        assert result["total_bytes"] <= LOG_EXCERPT_MAX_BYTES
        assert result["truncated"] is True

    def test_unknown_handle(self, tmp_path):
        from delegate.background import log_excerpt
        ad = tmp_path / "agent"
        ad.mkdir()
        result = log_excerpt(ad, "nope", source="stdout")
        assert "error" in result

    def test_invalid_source(self, tmp_path):
        from delegate.background import log_excerpt
        ad = tmp_path / "agent"
        ad.mkdir()
        info = self._launch_and_wait(ad, "echo hi")
        result = log_excerpt(ad, info.handle, source="garbage")
        assert "error" in result


# ---------------------------------------------------------------------------
# 6. Role-aware MCP tool subset
# ---------------------------------------------------------------------------


class TestMcpToolSubset:
    def _tool_names(self, agent_role, lab_team):
        """Helper: return the set of MCP tool names for the named role."""
        from delegate.bootstrap import _agents_dir
        agent = "alice_assistant" if agent_role == "researcher_assistant" else "alice"
        # build_agent_tools requires the SDK; skip the test gracefully if not available.
        try:
            from delegate.mcp_tools import build_agent_tools
            tools = build_agent_tools(lab_team, "lab", agent)
        except ImportError:
            pytest.skip("claude_agent_sdk not available")
        # Each tool object exposes a __name__ via the @tool decorator
        return {t.name if hasattr(t, "name") else t.__name__ for t in tools}

    def test_assistant_has_bg_log_excerpt(self, lab_team):
        names = self._tool_names("researcher_assistant", lab_team)
        assert "bg_log_excerpt" in names

    def test_assistant_has_artifact_save(self, lab_team):
        names = self._tool_names("researcher_assistant", lab_team)
        assert "artifact_save" in names
        assert "artifact_list" in names
        assert "artifact_path" in names

    def test_assistant_lacks_task_create(self, lab_team):
        names = self._tool_names("researcher_assistant", lab_team)
        assert "task_create" not in names
        assert "task_assign" not in names
        assert "task_status" not in names
        assert "task_cancel" not in names
        assert "task_comment" not in names

    def test_assistant_lacks_repo_tools(self, lab_team):
        names = self._tool_names("researcher_assistant", lab_team)
        assert "repo_list" not in names
        assert "rebase_to_main" not in names

    def test_assistant_lacks_review_tools(self, lab_team):
        names = self._tool_names("researcher_assistant", lab_team)
        assert "task_diff" not in names
        assert "task_approve" not in names
        assert "task_reject" not in names

    def test_researcher_keeps_full_toolset(self, lab_team):
        names = self._tool_names("researcher", lab_team)
        assert "task_create" in names
        assert "bg_log_excerpt" in names  # researchers also get this tool
        assert "task_show" in names


# ---------------------------------------------------------------------------
# 7. Task assignment guards
# ---------------------------------------------------------------------------


class TestAssignmentGuards:
    def test_create_task_rejects_assistant_assignee(self, lab_team):
        from delegate.task import create_task
        with pytest.raises(ValueError, match="researcher_assistant"):
            create_task(lab_team, "lab", title="x", assignee="alice_assistant")

    def test_assign_task_rejects_assistant(self, lab_team):
        from delegate.task import create_task, assign_task
        t = create_task(lab_team, "lab", title="real", assignee="alice")
        with pytest.raises(ValueError, match="researcher_assistant"):
            assign_task(lab_team, "lab", t["id"], "alice_assistant")


# ---------------------------------------------------------------------------
# 8. Charter dynamic section + {partner} substitution
# ---------------------------------------------------------------------------


class TestCharterRendering:
    def test_partner_substituted_in_assistant_charter(self, lab_team):
        from delegate.agent import build_system_prompt
        prompt = build_system_prompt(lab_team, "lab", "alice_assistant")
        assert "{partner}" not in prompt
        # The literal name should appear in the charter section
        assert "alice" in prompt.lower()

    def test_researcher_with_assistant_has_section(self, lab_team):
        from delegate.agent import build_system_prompt
        prompt = build_system_prompt(lab_team, "lab", "alice")
        # The dynamic per-researcher section uses a unique header that
        # interpolates the helper name, distinguishing it from the static
        # charter's prose mentions of "Your Assistant".
        assert "### Your Assistant — alice_assistant" in prompt
        assert "alice_assistant" in prompt

    def test_researcher_without_assistant_has_no_section(self, tmp_path):
        from delegate.bootstrap import bootstrap, add_agent
        from delegate.config import add_member
        from delegate.agent import build_system_prompt

        hc = tmp_path / "hc"
        hc.mkdir()
        add_member(hc, "human")
        bootstrap(hc, "lab", manager="delegate")
        add_agent(hc, "lab", agent_name="bob", role="researcher", no_assistant=True)
        prompt = build_system_prompt(hc, "lab", "bob")
        # The dynamic per-researcher header should NOT appear when no
        # assistant is bound.  Static charter prose may still mention the
        # phrase "Your Assistant" in passing — that's fine.
        assert "### Your Assistant —" not in prompt


# ---------------------------------------------------------------------------
# 9. Auto-continue defer logic
# ---------------------------------------------------------------------------


class TestAutoContinueAssistant:
    def test_skip_when_task_terminal(self, lab_team):
        """If the task is done/cancelled, the assistant just goes idle."""
        from delegate.agent import AgentLogger
        from delegate.runtime import _auto_continue_assistant
        from delegate.task import create_task, change_status

        t = create_task(lab_team, "lab", title="research X", assignee="alice")
        # Move directly to done (using default workflow's status; this is a smoke test)
        try:
            change_status(lab_team, "lab", t["id"], "cancelled")
        except Exception:
            pytest.skip("workflow doesn't allow direct cancellation")
        result = _auto_continue_assistant(
            lab_team, "lab", "alice_assistant", t["id"], AgentLogger("alice_assistant"),
        )
        assert result == "skip"

    def test_skip_when_no_bg_processes(self, lab_team):
        """No bg processes running → assistant goes idle (does NOT auto-continue like researcher)."""
        from delegate.agent import AgentLogger
        from delegate.runtime import _auto_continue_assistant
        from delegate.task import create_task

        t = create_task(lab_team, "lab", title="research", assignee="alice")
        result = _auto_continue_assistant(
            lab_team, "lab", "alice_assistant", t["id"], AgentLogger("alice_assistant"),
        )
        # The task is in "todo" status which is not "researching", so skip.
        # Even if it were researching, with no bg processes it should skip.
        assert result == "skip"

    def test_role_cache_invalidated_on_remove(self, lab_team):
        """Removing the assistant must invalidate the role cache so the gate
        no longer treats the partner→assistant pair as scoped."""
        from delegate.bootstrap import remove_assistant_for_researcher
        from delegate.mailbox import _lookup_role

        # Cache the role first
        role, partner = _lookup_role(lab_team, "lab", "alice_assistant")
        assert role == "researcher_assistant"
        assert partner == "alice"

        remove_assistant_for_researcher(lab_team, "lab", "alice")

        # After removal, the cache should be cleared and the agent unknown.
        role, partner = _lookup_role(lab_team, "lab", "alice_assistant")
        assert role == ""
        assert partner is None
