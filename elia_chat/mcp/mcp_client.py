"""MCP client for connecting to and communicating with MCP servers."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, AsyncGenerator
from enum import Enum

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from elia_chat.mcp.mcp_config import MCPServerConfig

logger = logging.getLogger(__name__)


class MCPConnectionStatus(Enum):
    """MCP client connection status."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class MCPClientError(Exception):
    """Base exception for MCP client errors."""
    pass


class MCPConnectionError(MCPClientError):
    """Exception raised when MCP connection fails."""
    pass


class MCPToolError(MCPClientError):
    """Exception raised when MCP tool execution fails."""
    pass


class MCPClient:
    """Client for connecting to and communicating with MCP servers."""
    
    def __init__(self, server_name: str, config: MCPServerConfig):
        """Initialize MCP client.
        
        Args:
            server_name: Name of the MCP server
            config: Server configuration
        """
        self.server_name = server_name
        self.config = config
        self._session: Optional[ClientSession] = None
        self._status = MCPConnectionStatus.DISCONNECTED
        self._tools: List[Dict[str, Any]] = []
        self._connection_error: Optional[str] = None
        
    @property
    def status(self) -> MCPConnectionStatus:
        """Get current connection status."""
        return self._status
    
    @property
    def connection_error(self) -> Optional[str]:
        """Get last connection error message."""
        return self._connection_error
    
    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._status == MCPConnectionStatus.CONNECTED
    
    async def connect(self) -> bool:
        """Establish connection to MCP server.
        
        Returns:
            True if connection successful, False otherwise
        """
        if self._status == MCPConnectionStatus.CONNECTED:
            return True
            
        self._status = MCPConnectionStatus.CONNECTING
        self._connection_error = None
        
        try:
            # Create server parameters for stdio transport
            server_params = StdioServerParameters(
                command=self.config.command,
                args=self.config.args,
                env=self.config.env
            )
            
            # Create stdio client session
            async with stdio_client(server_params) as (read, write):
                self._session = ClientSession(read, write)
                
                # Initialize the session
                await self._session.initialize()
                
                # List available tools
                await self._refresh_tools()
                
                self._status = MCPConnectionStatus.CONNECTED
                logger.info(f"Successfully connected to MCP server '{self.server_name}'")
                return True
                
        except Exception as e:
            error_msg = f"Failed to connect to MCP server '{self.server_name}': {e}"
            logger.error(error_msg)
            self._connection_error = str(e)
            self._status = MCPConnectionStatus.ERROR
            return False
    
    async def disconnect(self) -> None:
        """Close connection to MCP server."""
        if self._session:
            try:
                # Close the session if it has a close method
                if hasattr(self._session, 'close'):
                    await self._session.close()
            except Exception as e:
                logger.warning(f"Error closing MCP session for '{self.server_name}': {e}")
            finally:
                self._session = None
                
        self._status = MCPConnectionStatus.DISCONNECTED
        self._tools = []
        logger.info(f"Disconnected from MCP server '{self.server_name}'")
    
    async def _refresh_tools(self) -> None:
        """Refresh the list of available tools from the server."""
        if not self._session:
            raise MCPConnectionError("Not connected to MCP server")
            
        try:
            # List tools from the server
            result = await self._session.list_tools()
            
            # Convert MCP tool format to LiteLLM-compatible format
            self._tools = []
            for tool in result.tools:
                tool_def = {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": tool.inputSchema or {
                            "type": "object",
                            "properties": {},
                            "required": []
                        }
                    }
                }
                self._tools.append(tool_def)
                
            logger.debug(f"Loaded {len(self._tools)} tools from MCP server '{self.server_name}'")
            
        except Exception as e:
            error_msg = f"Failed to refresh tools from MCP server '{self.server_name}': {e}"
            logger.error(error_msg)
            raise MCPToolError(error_msg) from e
    
    async def list_tools(self) -> List[Dict[str, Any]]:
        """Get available tools from MCP server.
        
        Returns:
            List of tool definitions in LiteLLM-compatible format
            
        Raises:
            MCPConnectionError: If not connected to server
        """
        if not self.is_connected:
            raise MCPConnectionError(f"Not connected to MCP server '{self.server_name}'")
            
        return self._tools.copy()
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool on the MCP server.
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            
        Returns:
            Tool execution result
            
        Raises:
            MCPConnectionError: If not connected to server
            MCPToolError: If tool execution fails
        """
        if not self.is_connected or not self._session:
            raise MCPConnectionError(f"Not connected to MCP server '{self.server_name}'")
        
        # Validate that the tool exists
        tool_names = [tool["function"]["name"] for tool in self._tools]
        if tool_name not in tool_names:
            raise MCPToolError(f"Tool '{tool_name}' not found on server '{self.server_name}'. Available tools: {tool_names}")
        
        try:
            # Execute the tool with timeout
            result = await asyncio.wait_for(
                self._session.call_tool(tool_name, arguments),
                timeout=self.config.timeout
            )
            
            # Format the result
            if result.isError:
                error_msg = f"Tool '{tool_name}' execution failed: {result.content}"
                logger.error(error_msg)
                raise MCPToolError(error_msg)
            
            # Extract content from result
            content = ""
            if hasattr(result, 'content') and result.content:
                if isinstance(result.content, list):
                    # Handle multiple content items
                    content_parts = []
                    for item in result.content:
                        if hasattr(item, 'text'):
                            content_parts.append(item.text)
                        elif isinstance(item, dict) and 'text' in item:
                            content_parts.append(item['text'])
                        else:
                            content_parts.append(str(item))
                    content = "\n".join(content_parts)
                else:
                    content = str(result.content)
            
            logger.debug(f"Tool '{tool_name}' executed successfully on server '{self.server_name}'")
            
            return {
                "success": True,
                "content": content,
                "tool_name": tool_name,
                "server_name": self.server_name
            }
            
        except asyncio.TimeoutError:
            error_msg = f"Tool '{tool_name}' execution timed out after {self.config.timeout} seconds"
            logger.error(error_msg)
            raise MCPToolError(error_msg)
        except Exception as e:
            error_msg = f"Tool '{tool_name}' execution failed on server '{self.server_name}': {e}"
            logger.error(error_msg)
            raise MCPToolError(error_msg) from e
    
    def get_tool_by_name(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Get tool definition by name.
        
        Args:
            tool_name: Name of the tool
            
        Returns:
            Tool definition or None if not found
        """
        for tool in self._tools:
            if tool["function"]["name"] == tool_name:
                return tool
        return None
    
    def is_tool_auto_approved(self, tool_name: str) -> bool:
        """Check if a tool is in the auto-approve list.
        
        Args:
            tool_name: Name of the tool
            
        Returns:
            True if tool is auto-approved, False otherwise
        """
        return tool_name in self.config.auto_approve
    
    async def health_check(self) -> bool:
        """Perform a health check on the connection.
        
        Returns:
            True if connection is healthy, False otherwise
        """
        if not self.is_connected or not self._session:
            return False
            
        try:
            # Try to list tools as a health check
            await self._session.list_tools()
            return True
        except Exception as e:
            logger.warning(f"Health check failed for MCP server '{self.server_name}': {e}")
            self._status = MCPConnectionStatus.ERROR
            self._connection_error = str(e)
            return False
    
    def __repr__(self) -> str:
        """String representation of the MCP client."""
        return f"MCPClient(server='{self.server_name}', status={self.status.value}, tools={len(self._tools)})"