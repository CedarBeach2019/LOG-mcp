"""
Base scout connector for sending dehydrated prompts to external agents.
"""
from abc import ABC, abstractmethod
import asyncio
from typing import Optional, AsyncGenerator, Union
import logging

logger = logging.getLogger(__name__)

class ScoutBase(ABC):
    """Abstract base class for scout connectors."""
    
    def __init__(self, api_key: Optional[str] = None, **kwargs):
        """
        Initialize the scout with provider-specific configuration.
        
        Args:
            api_key: Provider API key (can be set via environment variable)
            **kwargs: Additional provider-specific arguments
        """
        self.api_key = api_key
        self.config = kwargs
        
    @abstractmethod
    async def send(
        self, 
        prompt: str, 
        system_message: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Send a dehydrated prompt to the provider's API and return the response.
        
        Args:
            prompt: The dehydrated prompt string to send
            system_message: Optional system message to guide the assistant
            **kwargs: Additional provider-specific parameters
            
        Returns:
            The raw response text from the provider
        """
        pass
    
    @abstractmethod
    async def stream(
        self, 
        prompt: str, 
        system_message: Optional[str] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        Stream the response from the provider's API.
        
        Args:
            prompt: The dehydrated prompt string to send
            system_message: Optional system message to guide the assistant
            **kwargs: Additional provider-specific parameters
            
        Yields:
            Chunks of the response as they arrive
        """
        pass
    
    def _validate_api_key(self) -> None:
        """Validate that an API key is available."""
        if not self.api_key:
            raise ValueError(
                f"API key is required for {self.__class__.__name__}. "
                "Set it via the api_key parameter or environment variable."
            )
    
    def _handle_error(self, error: Exception, context: str = "") -> None:
        """Log and potentially handle API errors."""
        logger.error(f"Error in {self.__class__.__name__} {context}: {error}")
        # Re-raise the error for the caller to handle
        raise
