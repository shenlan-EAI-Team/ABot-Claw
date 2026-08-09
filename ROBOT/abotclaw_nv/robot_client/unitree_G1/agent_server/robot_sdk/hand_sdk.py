"""Hand control client for G1 dexterous hands.

This module provides a client to control the G1 robot's dexterous hands
via TCP connection to the hand server running on the robot.
"""
# echo "7" | nc 192.168.123.164 5678

import socket
from typing import Optional

try:
    from .config import get_g1_robot_ip
except ImportError:
    from config import get_g1_robot_ip

DEFAULT_PORT = 5678

# Command descriptions
COMMANDS = {
    '1':  'Left hand open',
    '2':  'Left hand fist',
    '3':  'Left hand half grip',
    '4':  'Left hand pinch',
    '5':  'Left hand bottle grip',
    '6':  'Left hand peace sign',
    '7':  'Right hand open',
    '8':  'Right hand fist',
    '9':  'Right hand half grip',
    '10': 'Right hand pinch',
    '11': 'Right hand bottle grip',
    '12': 'Right hand peace sign',
    '13': 'Both hands open',
    '14': 'Both hands fist',
    '15': 'Both hands half grip',
    '16': 'Both hands pinch',
    '17': 'Both hands bottle grip',
    '18': 'Both hands soft grip',
    '19': 'Both hands alternating',
    's':  'Query status',
    'status': 'Query status',
}


class HandClient:
    """Client for controlling G1 dexterous hands."""
    
    def __init__(self, host: str, port: int = DEFAULT_PORT):
        """Initialize hand client.
        
        Args:
            host: Robot IP address
            port: Server port (default: 5678)
        """
        self.host = host
        self.port = port
        self.sock = None

    def connect(self, timeout: float = 5.0) -> None:
        """Connect to the hand server.
        
        Args:
            timeout: Connection timeout in seconds
            
        Raises:
            ConnectionRefusedError: If connection is refused
            socket.timeout: If connection times out
        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect((self.host, self.port))
        self.sock.settimeout(10.0)

    def send(self, cmd: str) -> str:
        """Send command to hand server.

        Args:
            cmd: Command string (see COMMANDS dict)

        Returns:
            Server response string
        """
        self.sock.sendall(f"{cmd}\n".encode())
        return self.sock.recv(4096).decode().strip()

    def close(self) -> None:
        """Close the connection."""
        if self.sock:
            self.sock.close()
            self.sock = None

    def __enter__(self):
        self.connect()
        return self
        
    def __exit__(self, *args):
        self.close()


def send_hand_command(cmd: str, robot_ip: Optional[str] = None, port: int = DEFAULT_PORT) -> str:
    """Convenience function to send a single hand command.
    
    Args:
        cmd: Command to send (e.g., '11' for right hand bottle grip)
        robot_ip: Robot IP address
        port: Server port
        
    Returns:
        Server response
    """
    with HandClient(robot_ip or get_g1_robot_ip(), port) as client:
        return client.send(cmd)

if __name__ == "__main__":
    send_hand_command("7")