"""Authentication Module
This module provides authentication utilities for verifying requests from Server A,
including JWT token validation and internal key verification.
"""

import os
import logging
from functools import wraps
from flask import request, g
from dotenv import load_dotenv 
import jwt
import datetime
# Load environment variables
load_dotenv()

# Get logger for this module
logger = logging.getLogger(__name__)

# Get JWT secret key and internal API key from environment variables
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-jwt-secret-key-here")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "your-internal-api-key-here")

def verify_token(token):
    """
    Verifies the JWT token sent from Server A.
    
    Args:
        token (str): The JWT token to verify.
        
    Returns:
        tuple: (is_valid, payload or error_message)
    """
    try:
        # Decode and verify the token
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
        return True, payload
    except jwt.ExpiredSignatureError:
        return False, "Token has expired"
    except jwt.InvalidTokenError as e:
        return False, f"Invalid token: {str(e)}"
    except Exception as e:
        logger.error(f"Token verification error: {str(e)}", exc_info=True)
        return False, "Token verification failed"

def verify_internal_key(key):
    """
    Verifies the internal API key sent from Server A.
    
    Args:
        key (str): The internal API key to verify.
        
    Returns:
        bool: True if the key is valid, False otherwise.
    """
    return key == INTERNAL_API_KEY

def require_auth(f):
    """
    Decorator for routes that require authentication.
    Verifies both JWT token and internal API key.
    
    Usage:
        @app.route('/protected')
        @require_auth
        def protected_route():
            # Access user info with g.user
            return "Protected data"
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            logger.warning("Missing or invalid Authorization header", 
                          extra={"remote_ip": request.remote_addr})
            return {
                "status": False, 
                "message": "Authentication required"
            }, 401
            
        token = auth_header.split(' ')[1]
        
        # Get internal key from X-Internal-Key header
        internal_key = request.headers.get('X-Internal-Key')
        if not internal_key:
            logger.warning("Missing X-Internal-Key header", 
                          extra={"remote_ip": request.remote_addr})
            return {
                "status": False, 
                "message": "Internal key required"
            }, 401
            
        # Verify internal key
        if not verify_internal_key(internal_key):
            logger.warning("Invalid internal key", 
                          extra={"remote_ip": request.remote_addr})
            return {
                "status": False, 
                "message": "Invalid internal key"
            }, 403
            
        # Verify JWT token
        is_valid, result = verify_token(token)
        if not is_valid:
            logger.warning(f"Invalid token: {result}", 
                          extra={"remote_ip": request.remote_addr})
            return {
                "status": False, 
                "message": result
            }, 401
            
        # Store user info in Flask's g object for use in the route handler
        g.user = result
        
        # Call the original route function
        return f(*args, **kwargs)
    
    return decorated
def generate_jwt_token(user_id, username, email=None, secret=None, expires_in=3600):
    """
    Generate a JWT token for testing or internal use.
    """
    if secret is None:
        # Use your actual secret key here or import from config
        secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        raise ValueError("Secret key is required for token generation")
    payload = {
        "userId": user_id,
        "username": username,
        "email": email or f"{username}@example.com",
        "iat": int(datetime.datetime.utcnow().timestamp()),
        "exp": int((datetime.datetime.utcnow() + datetime.timedelta(seconds=expires_in)).timestamp())
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    # For PyJWT >= 2.0, jwt.encode returns a string, otherwise bytes
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token