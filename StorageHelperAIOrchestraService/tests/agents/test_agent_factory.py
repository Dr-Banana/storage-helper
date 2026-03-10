"""
Tests for AgentFactory — verifies intent-to-agent routing, especially the changes
that added MODIFY_RECIPE and mapped COOKING_STEPS / RECIPE_QA / MODIFY_RECIPE to a
shared GeneralAgent stub instead of dedicated heavy agents.
"""
import pytest
from app.modules.intent_classifier import Intent
from app.agents.agent_factory import AgentFactory
from app.agents.general_agent import GeneralAgent
from app.agents.plan_ahead_agent import PlanAheadAgent
from app.agents.search_agent import SearchAgent


@pytest.fixture
def factory():
    """Create a fresh AgentFactory instance (bypassing the module-level singleton)."""
    instance = object.__new__(AgentFactory)
    instance._initialize_agents()
    return instance


class TestAgentFactoryRouting:
    """Verify that get_agent() returns the expected agent type for each intent."""

    def test_plan_ahead_returns_plan_ahead_agent(self, factory):
        agent = factory.get_agent(Intent.PLAN_AHEAD)
        assert isinstance(agent, PlanAheadAgent)

    def test_search_returns_search_agent(self, factory):
        agent = factory.get_agent(Intent.SEARCH)
        assert isinstance(agent, SearchAgent)

    def test_general_returns_general_agent(self, factory):
        agent = factory.get_agent(Intent.GENERAL)
        assert isinstance(agent, GeneralAgent)

    def test_cooking_steps_returns_general_agent_stub(self, factory):
        """COOKING_STEPS is handled in chat.py; the factory returns a GeneralAgent stub."""
        agent = factory.get_agent(Intent.COOKING_STEPS)
        assert isinstance(agent, GeneralAgent)

    def test_recipe_qa_returns_general_agent_stub(self, factory):
        """RECIPE_QA is handled in chat.py; the factory returns a GeneralAgent stub."""
        agent = factory.get_agent(Intent.RECIPE_QA)
        assert isinstance(agent, GeneralAgent)

    def test_modify_recipe_returns_general_agent_stub(self, factory):
        """MODIFY_RECIPE is handled in chat.py; the factory returns a GeneralAgent stub."""
        agent = factory.get_agent(Intent.MODIFY_RECIPE)
        assert isinstance(agent, GeneralAgent)

    def test_cooking_steps_recipe_qa_modify_recipe_share_same_instance(self, factory):
        """All three special intents that bypass route_by_intent share the same GeneralAgent
        instance (created once in _initialize_agents), so there is no redundant object creation."""
        cs = factory.get_agent(Intent.COOKING_STEPS)
        rqa = factory.get_agent(Intent.RECIPE_QA)
        mr = factory.get_agent(Intent.MODIFY_RECIPE)
        general = factory.get_agent(Intent.GENERAL)
        assert cs is rqa is mr is general

    def test_unknown_intent_falls_back_to_general(self, factory):
        """get_agent() with an unmapped intent returns GeneralAgent as fallback."""
        # We pass a valid Intent not in the map by temporarily removing it
        agents_backup = factory._agents.copy()
        del factory._agents[Intent.UPDATE]
        try:
            agent = factory.get_agent(Intent.UPDATE)
            assert isinstance(agent, GeneralAgent)
        finally:
            factory._agents = agents_backup

    def test_get_all_agents_contains_modify_recipe(self, factory):
        """get_all_agents() must include MODIFY_RECIPE in the returned mapping."""
        all_agents = factory.get_all_agents()
        assert Intent.MODIFY_RECIPE in all_agents

    def test_get_all_agents_returns_copy(self, factory):
        """Mutating the returned dict must not affect the factory's internal state."""
        all_agents = factory.get_all_agents()
        all_agents[Intent.PLAN_AHEAD] = GeneralAgent()
        assert not isinstance(factory.get_agent(Intent.PLAN_AHEAD), GeneralAgent)

    def test_plan_cook_home_sub_agent_lazy_loaded(self, factory):
        """get_plan_cook_home_sub_agent() is lazily initialized on first call."""
        from app.agents.plan_cook_home_agent import PlanCookHomeAgent
        assert factory._plan_cook_home_sub_agent is None
        sub = factory.get_plan_cook_home_sub_agent()
        assert isinstance(sub, PlanCookHomeAgent)
        # Second call returns the same cached instance
        assert factory.get_plan_cook_home_sub_agent() is sub
