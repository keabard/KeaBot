""" 
HoNCore. Python library providing connectivity and functionality
with HoN's chat server.

Custom exceptions to be raised by this library and caught by any program using this library.
Helps debug login/logout events/incorrect data and socket errors (timeout, broken pipes etc).

TODO     
     * urllib2 sometimes throws BadStatusLine which means the server responded with an unknown HTTP status code. 
       Needs to be handled correctly, it should throw a MasterServerError 109, but it needs a stronger definition.
"""

class HoNException(Exception):
    """ Base exception for all exceptions for this library. """
    def __init__(self, code, *args):
        self.code = code
        self.error = _errormap[code]
    
    def __str__(self):
        return repr("Error %d: %s" % (self.code, self.error))

class HoNCoreError(HoNException):
    """ Exception to be used for honcore internals such as a socket error which will be handled
        by something else inside honcore, or when a client tries to connect a method to an event
        that does not exist.
    """
    pass

class HoNConfigError(HoNException):
    """ Exception relating to the configuration data.
        Can be raised if the configuration passed does not satisfy the requirements.
    """
    pass
    
class MasterServerError(HoNException):
    """ Exceptions related to the master server. 
        Can be raised if invalid data is returned or if the connection times out.
    """
    pass

class ChatServerError(HoNException):
    """ Exceptions related to the chat server.
        Can be raised if invalid data is received or if the socket times out and the
        connection to the server is lost.
    """
    pass

class GameServerError(HoNException):
    """ Exceptions related to a game server.
        Can be raised if invalid data is received or if the socket times out and the
        connection to the server is lost.
    """
    pass

_errormap = {
    10  : 'Socket error.',
    11  : 'Socket timed out.',
    12  : 'Unknown packet received',
    13  : 'Unknown event ID',
    14  : 'Method is not connected to this event ID.',
    100 : 'Could not connect to the masterserver.',
    101 : 'Could not obtain login data.',
    102 : 'Incorrect username/password.',
    103 : 'Failed to get login data after 3 attempts.',
    104 : 'Connection to the master server timed out.',
    105 : 'Connection to the master server was rejected.',
    106 : 'Master server failed to receieve logout request, forcing logout.',
    107 : 'Requester HTTP error.', # Don't leave this in, expand it to handle each different HTTP/URL Error?
    108 : 'Unexpected opcode when parsing PHP serialisation.',
    109 : 'Bad HTTP status code.',
    110 : 'Connection reset by peer', # Good sign it's down, it's dropping connections?
    111 : 'Connection refused', # Very good sign it's down, it's refusing connections?
    112 : 'Connection timed out',
    113 : 'Message of the day data error',
    114 : 'No address associate with hostname',
    120 : 'No buddies found',
    121 : 'No ban list found',
    122 : 'No ignored users found',
    123 : 'No clan members found',
    200 : 'Server did not respond to authentication request.',
    201 : 'Connection to the chat server timed out.',
    202 : 'Connection to the chat server was rejected.',
    203 : 'Failed to connect to the chat server after 3 attempts.',
    204 : 'Empty packet received.',
    205 : 'No cookie/auth hash provided.',
    206 : 'Broken Pipe, is the chat version correct?',
    207 : 'Server error, connection lost.',
    208 : 'Could not connect to the chat server.',
    209 : 'Socket was not connected.',
}

