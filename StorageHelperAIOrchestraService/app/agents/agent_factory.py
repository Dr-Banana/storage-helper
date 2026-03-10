"""
AgentFactory: Agent factory class for creating and managing different agent instances
"""
from typing import Dict, Optional
from app.modules.intent_classifier import Intent
from app.agents.base import BaseAgent
from app.agents.search_agent import SearchAgent
from app.agents.update_agent import UpdateAgent
from app.agents.plan_eat_out_agent import PlanEatOutAgent
from app.agents.plan_cook_home_agent import PlanCookHomeAgent
from app.agents.plan_ahead_agent import PlanAheadAgent
from app.agents.general_agent import GeneralAgent


class AgentFactory:
    """
    Agent Factory: Singleton pattern, manages all agent instances
    """
    
    _instance: Optional['AgentFactory'] = None
    _agents: Dict[Intent, BaseAgent] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize_agents()
        return cls._instance
    
    def _initialize_agents(self):
        """Initialize all agent instances. Plan Cook Home is a sub-agent of Plan Ahead, not a top-level intent."""
        _general = GeneralAgent()
        self._agents = {
            Intent.SEARCH: SearchAgent(),
            Intent.UPDATE: UpdateAgent(),
            Intent.PLAN_EAT_OUT: PlanEatOutAgent(),
            Intent.PLAN_AHEAD: PlanAheadAgent(),
            # COOKING_STEPS, RECIPE_QA, and MODIFY_RECIPE are handled directly in
            # chat.py BEFORE route_by_intent is called.  Mapping them to GeneralAgent
            # here ensures route_by_intent returns a no-op stub instead of re-running
            # the heavy agent logic a second time.
            Intent.COOKING_STEPS: _general,
            Intent.RECIPE_QA: _general,
            Intent.MODIFY_RECIPE: _general,
            Intent.GENERAL: _general,
        }
        self._plan_cook_home_sub_agent: Optional[PlanCookHomeAgent] = None

    def get_plan_cook_home_sub_agent(self) -> PlanCookHomeAgent:
        """Return the Plan Cook Home sub-agent (used under PLAN_AHEAD for inventory and recipe suggestions)."""
        if self._plan_cook_home_sub_agent is None:
            self._plan_cook_home_sub_agent = PlanCookHomeAgent()
        return self._plan_cook_home_sub_agent

    def get_agent(self, intent: Intent) -> BaseAgent:
        """
        Get the corresponding agent based on intent
        
        Args:
            intent: Intent enum
            
        Returns:
            Corresponding agent instance
        """
        agent = self._agents.get(intent)
        if agent is None:
            # If no corresponding agent is found, return GeneralAgent
            return self._agents[Intent.GENERAL]
        return agent
    
    def get_all_agents(self) -> Dict[Intent, BaseAgent]:
        """
        Get all agents
        
        Returns:
            Dictionary of all agents
        """
        return self._agents.copy()


# Singleton instance
agent_factory = AgentFactory()
