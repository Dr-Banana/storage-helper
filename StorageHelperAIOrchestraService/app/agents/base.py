"""
Base Agent class: Defines common interface and functionality for all agents
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    Base class for all agents, defines a unified interface
    """
    
    def __init__(self, name: str):
        """
        Initialize the agent
        
        Args:
            name: The name of the agent
        """
        self.name = name
        self.logger = logging.getLogger(f"{__name__}.{name}")
    
    @abstractmethod
    async def execute(
        self,
        user_input: str,
        owner_id: int,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute the main logic of the agent
        
        Args:
            user_input: User input
            owner_id: User ID
            **kwargs: Other optional parameters
            
        Returns:
            Dictionary containing action, message, and data
        """
        pass
    
    def get_action_name(self) -> str:
        """
        Return the action name corresponding to this agent
        
        Returns:
            Action name string
        """
        return self.name
    
    def format_response(
        self,
        action: str,
        message: str,
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Format response to a unified format
        
        Args:
            action: Action type
            message: Message
            data: Data
            
        Returns:
            Formatted response dictionary
        """
        return {
            "action": action,
            "message": message,
            "data": data
        }
