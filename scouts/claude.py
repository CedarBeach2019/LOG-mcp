"""
Claude (Anthropic) scout connector.
"""
import os
from typing import Optional, AsyncGenerator
import anthropic
from anthropic import AsyncAnthropic, APIError, RateLimitError, APIConnectionError
import asyncio

from scouts.base import ScoutBase

class ClaudeScout(ScoutBase):
    """Scout connector for Anthropic's Claude API."""
    
    def __init__(
        self, 
        api_key: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs
    ):
        """
        Initialize Claude scout.
        
        Args:
            api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var)
            model: Claude model to use
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0.0 to 1.0)
            **kwargs: Additional arguments passed to ScoutBase
        """
        # Use provided api_key or fall back to environment variable
        if api_key is None:
            api_key = os.getenv("ANTHROPIC_API_KEY")
        
        super().__init__(api_key=api_key, **kwargs)
        
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        
        # Initialize the async client
        self._client = None
        
    @property
    def client(self) -> AsyncAnthropic:
        """Lazy initialization of the Anthropic client."""
        if self._client is None:
            self._validate_api_key()
            self._client = AsyncAnthropic(api_key=self.api_key)
        return self._client
    
    async def send(
        self, 
        prompt: str, 
        system_message: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Send a dehydrated prompt to Claude API and return the response.
        
        Args:
            prompt: The dehydrated prompt string to send
            system_message: Optional system message
            **kwargs: Additional parameters to override defaults
            
        Returns:
            The raw response text from Claude
        """
        try:
            # Merge default parameters with overrides
            model = kwargs.get('model', self.model)
            max_tokens = kwargs.get('max_tokens', self.max_tokens)
            temperature = kwargs.get('temperature', self.temperature)
            
            # Prepare the message
            messages = [{"role": "user", "content": prompt}]
            
            # Prepare system parameter if provided
            system_param = {"system": system_message} if system_message else {}
            
            # Make the API call
            response = await self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
                **system_param,
                **{k: v for k, v in kwargs.items() 
                   if k not in ['model', 'max_tokens', 'temperature']}
            )
            
            # Extract and return the response text
            if response.content and len(response.content) > 0:
                return response.content[0].text
            else:
                return ""
                
        except (APIError, RateLimitError, APIConnectionError) as e:
            self._handle_error(e, "while sending to Claude API")
            return ""  # This line won't be reached due to _handle_error raising
    
    async def stream(
        self, 
        prompt: str, 
        system_message: Optional[str] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        Stream the response from Claude API.
        
        Args:
            prompt: The dehydrated prompt string to send
            system_message: Optional system message
            **kwargs: Additional parameters
            
        Yields:
            Chunks of the response as they arrive
        """
        try:
            # Merge parameters
            model = kwargs.get('model', self.model)
            max_tokens = kwargs.get('max_tokens', self.max_tokens)
            temperature = kwargs.get('temperature', self.temperature)
            
            messages = [{"role": "user", "content": prompt}]
            system_param = {"system": system_message} if system_message else {}
            
            # Make streaming API call
            stream = await self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
                stream=True,
                **system_param,
                **{k: v for k, v in kwargs.items() 
                   if k not in ['model', 'max_tokens', 'temperature']}
            )
            
            async for chunk in stream:
                if chunk.type == 'content_block_delta':
                    yield chunk.delta.text
                    
        except (APIError, RateLimitError, APIConnectionError) as e:
            self._handle_error(e, "while streaming from Claude API")
