"""
DeepSeek scout connector.
"""
import os
from typing import Optional, AsyncGenerator
from openai import AsyncOpenAI, APIError, RateLimitError, APIConnectionError

from scouts.base import ScoutBase

class DeepSeekScout(ScoutBase):
    """Scout connector for DeepSeek API."""
    
    def __init__(
        self, 
        api_key: Optional[str] = None,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs
    ):
        """
        Initialize DeepSeek scout.
        
        Args:
            api_key: DeepSeek API key (defaults to DEEPSEEK_API_KEY env var)
            model: DeepSeek model to use
            base_url: API base URL
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0.0 to 1.0)
            **kwargs: Additional arguments passed to ScoutBase
        """
        # Use provided api_key or fall back to environment variable
        if api_key is None:
            api_key = os.getenv("DEEPSEEK_API_KEY")
        
        super().__init__(api_key=api_key, **kwargs)
        
        self.model = model
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.temperature = temperature
        
        # Initialize the async client
        self._client = None
        
    @property
    def client(self) -> AsyncOpenAI:
        """Lazy initialization of the OpenAI-compatible client."""
        if self._client is None:
            self._validate_api_key()
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
        return self._client
    
    async def send(
        self, 
        prompt: str, 
        system_message: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Send a dehydrated prompt to DeepSeek API and return the response.
        
        Args:
            prompt: The dehydrated prompt string to send
            system_message: Optional system message
            **kwargs: Additional parameters to override defaults
            
        Returns:
            The raw response text from DeepSeek
        """
        try:
            # Merge default parameters with overrides
            model = kwargs.get('model', self.model)
            max_tokens = kwargs.get('max_tokens', self.max_tokens)
            temperature = kwargs.get('temperature', self.temperature)
            
            # Prepare messages
            messages = []
            if system_message:
                messages.append({"role": "system", "content": system_message})
            messages.append({"role": "user", "content": prompt})
            
            # Make the API call
            response = await self.client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
                **{k: v for k, v in kwargs.items() 
                   if k not in ['model', 'max_tokens', 'temperature']}
            )
            
            # Extract and return the response text
            if response.choices and len(response.choices) > 0:
                return response.choices[0].message.content or ""
            else:
                return ""
                
        except (APIError, RateLimitError, APIConnectionError) as e:
            self._handle_error(e, "while sending to DeepSeek API")
            return ""  # This line won't be reached due to _handle_error raising
    
    async def stream(
        self, 
        prompt: str, 
        system_message: Optional[str] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        Stream the response from DeepSeek API.
        
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
            
            # Prepare messages
            messages = []
            if system_message:
                messages.append({"role": "system", "content": system_message})
            messages.append({"role": "user", "content": prompt})
            
            # Make streaming API call
            stream = await self.client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
                stream=True,
                **{k: v for k, v in kwargs.items() 
                   if k not in ['model', 'max_tokens', 'temperature']}
            )
            
            async for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        yield delta.content
                    
        except (APIError, RateLimitError, APIConnectionError) as e:
            self._handle_error(e, "while streaming from DeepSeek API")
