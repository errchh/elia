"""MCP configuration models and loading functionality."""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

from pydantic import BaseModel, Field, ValidationError, ConfigDict

from elia_chat.locations import config_directory

logger = logging.getLogger(__name__)


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""
    
    model_config = ConfigDict(extra="forbid")
    
    command: str
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    disabled: bool = False
    auto_approve: List[str] = Field(default_factory=list)
    timeout: int = 30


class MCPConfig(BaseModel):
    """Complete MCP configuration."""
    
    model_config = ConfigDict(extra="forbid")
    
    mcp_servers: Dict[str, MCPServerConfig] = Field(default_factory=dict)
    
    @classmethod
    def load_from_file(cls, config_path: Path) -> "MCPConfig":
        """Load MCP configuration from JSON file.
        
        Args:
            config_path: Path to the MCP configuration file
            
        Returns:
            MCPConfig instance
            
        Raises:
            FileNotFoundError: If the config file doesn't exist
            ValidationError: If the config file is invalid
            json.JSONDecodeError: If the JSON is malformed
        """
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            return cls(**config_data)
            
        except FileNotFoundError:
            logger.error(f"MCP configuration file not found: {config_path}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in MCP configuration file {config_path}: {e}")
            raise
        except ValidationError as e:
            logger.error(f"Invalid MCP configuration in {config_path}: {e}")
            raise
    
    @classmethod
    def load_default(cls) -> "MCPConfig":
        """Load MCP configuration from default location.
        
        Returns:
            MCPConfig instance. If the file doesn't exist or is invalid,
            returns an empty configuration with no servers.
        """
        config_path = config_directory() / "mcp.json"
        
        if not config_path.exists():
            logger.info(f"MCP configuration file not found at {config_path}, using empty configuration")
            return cls()
        
        try:
            return cls.load_from_file(config_path)
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"Failed to load MCP configuration from {config_path}: {e}")
            logger.warning("Using empty MCP configuration")
            return cls()
    
    def get_enabled_servers(self) -> Dict[str, MCPServerConfig]:
        """Return only enabled MCP servers.
        
        Returns:
            Dictionary of server names to their configurations, 
            excluding disabled servers.
        """
        return {
            name: config 
            for name, config in self.mcp_servers.items() 
            if not config.disabled
        }
    
    def save_to_file(self, config_path: Optional[Path] = None) -> None:
        """Save MCP configuration to JSON file.
        
        Args:
            config_path: Path to save the configuration. If None, uses default location.
            
        Raises:
            OSError: If the file cannot be written
        """
        if config_path is None:
            config_path = config_directory() / "mcp.json"
        
        # Ensure the parent directory exists
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self.model_dump(), f, indent=2)
            logger.info(f"MCP configuration saved to {config_path}")
        except OSError as e:
            logger.error(f"Failed to save MCP configuration to {config_path}: {e}")
            raise
    
    def add_server(self, name: str, config: MCPServerConfig) -> None:
        """Add or update an MCP server configuration.
        
        Args:
            name: Server name
            config: Server configuration
        """
        self.mcp_servers[name] = config
    
    def remove_server(self, name: str) -> bool:
        """Remove an MCP server configuration.
        
        Args:
            name: Server name to remove
            
        Returns:
            True if the server was removed, False if it didn't exist
        """
        if name in self.mcp_servers:
            del self.mcp_servers[name]
            return True
        return False
    
    def get_server(self, name: str) -> Optional[MCPServerConfig]:
        """Get configuration for a specific server.
        
        Args:
            name: Server name
            
        Returns:
            Server configuration or None if not found
        """
        return self.mcp_servers.get(name)