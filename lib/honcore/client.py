"""
HoNCore. Python library providing connectivity and functionality
with HoN's chat server.
"""

import sys, struct, socket, time
import deserialise, common
from common import Channel
from requester import Requester
from networking import ChatSocket, GameSocket
from utils import ping_server
from constants import *
from exceptions import *

__all__ = ['HoNClient']

_config_defaults = {
    "chatport" : 11031, 
    "protocol" : 21, 
    "invis" : False,
}

class HoNClient(object):    
    def __init__(self):
        self.config = _config_defaults
        self.__events = {}
        self.__game_events = {}
        self.__create_events()
        self.__setup_events()
        self.__chat_socket = ChatSocket(self.__events)
        self.__game_socket = GameSocket(self.__game_events)
        self.__listener = None
        self.__requester = Requester()
        self.account = None
        self.__channels = {}
        self.__users = {}

    def __create_events(self):
        """ Create each event that can be triggered by the client.
            As more packets are reverse engineered they should be added here so that 
            the client can handle them.
        """
        # Chat events
        self.__events[HON_SC_AUTH_ACCEPTED] = Event("Auth Accepted", HON_SC_AUTH_ACCEPTED)
        self.__events[HON_SC_PING] = Event("Ping", HON_SC_PING)
        self.__events[HON_SC_CHANNEL_MSG] = Event("Channel Message", HON_SC_CHANNEL_MSG)
        self.__events[HON_SC_JOINED_CHANNEL] = Event("Join Channel", HON_SC_JOINED_CHANNEL)
        self.__events[HON_SC_ENTERED_CHANNEL] = Event("Entered Channel", HON_SC_ENTERED_CHANNEL)
        self.__events[HON_SC_LEFT_CHANNEL] = Event("Left Channel", HON_SC_LEFT_CHANNEL)
        self.__events[HON_SC_WHISPER] = Event("Whisper", HON_SC_WHISPER)
        self.__events[HON_SC_PM] = Event("Private Message", HON_SC_PM)
        self.__events[HON_SC_MESSAGE_ALL] = Event("Server Message", HON_SC_MESSAGE_ALL)
        self.__events[HON_SC_TOTAL_ONLINE] = Event("Total Online", HON_SC_TOTAL_ONLINE)
        self.__events[HON_SC_GAME_INVITE] = Event("Game Invite", HON_SC_GAME_INVITE)
        self.__events[HON_SC_PACKET_RECV] = Event("Packet Received", HON_SC_PACKET_RECV)
        self.__events[HON_SC_REQUEST_NOTIFICATION] = Event("Buddy invite received", HON_SC_REQUEST_NOTIFICATION)
        
        # Game events
        self.__game_events[HON_GSC_AUTH_ACCEPTED] = Event("Game Auth Accepted", HON_GSC_AUTH_ACCEPTED)
        self.__game_events[HON_GSC_PING] = Event("Game Ping", HON_GSC_PING)
        self.__game_events[HON_GSC_PACKET_RECV] = Event("Game Packet Received", HON_GSC_PACKET_RECV)
        self.__game_events[HON_GSC_TIMEOUT] = Event("Game Server Timeout", HON_GSC_TIMEOUT)
        self.__game_events[HON_GSC_SERVER_STATE] = Event("Game Server State", HON_GSC_SERVER_STATE)
        self.__game_events[HON_GSC_SERVER_INFO] = Event("Game Server INFO", HON_GSC_SERVER_INFO)

    def __setup_events(self):
        """ Transparent handling of some data is needed so that the client
            can track things such as users and channels.
        """
        # Chat events
        self.connect_event(HON_SC_JOINED_CHANNEL, self.__on_joined_channel, priority=1)
        self.connect_event(HON_SC_ENTERED_CHANNEL, self.__on_entered_channel, priority=1)
        self.connect_event(HON_SC_LEFT_CHANNEL, self.__on_left_channel, priority=1)
        
        # Game events
        self.connect_game_event(HON_GSC_TIMEOUT, self.__on_game_timeout, priority=1)

    def __on_initial_statuses(self, users):
        """ Sets the status and flags for each user. """
        for account_id in users:
            if account_id in self.__users:
                user = self.__users[account_id]
                user.status = users[account_id]['status']
                user.flags = users[account_id]['flags']
    
    def __on_joined_channel(self, channel_name, channel_id, topic, operators, users):
        """ Channel names, channel ids, user nicks and user account ids need to be
            contained in a hash table/dict so they can be looked up later when needed.
        """
        channel = Channel(channel_id, channel_name, topic, operators, users)
        self.__channels[channel_id] = channel
        for user in users:
            if user.account_id not in self.__users:
                self.__users[user.account_id] = user

    def __on_entered_channel(self, channel_id, user):
        """ Transparently add the id and nick of the user who entered the channel to
            the users dictionary.
        """
        if user.account_id not in self.__users:
            self.__users[user.account_id] = user
        channel = self.__channels[channel_id]
        if user not in channel.users:
            channel.users.append(user)
            
    def __on_left_channel(self, channel_id, user_id):
        """ Transparently remove the id and nick of the user who left the channel to
            the users dictionary.
        """
        channel = self.__channels[channel_id]
        for user in channel.users:
            if user.account_id == user_id:
                channel.users.remove(user)
                break
                
        if user.account_id in self.__users:
            self.__users.pop(user.account_id)
        
        print 'User %s left channel %s'%(user.nickname, channel.name)
        
    def __on_game_timeout(self):
        """ Handle the game server timeout gently.
        """
        print 'Game server timed out, closing connection...'
        
        self.account.game_session_key = None
        self.account.game_ip = None
        self.account.game_port = None
        self.account.game_host_id = None
        self.account.acc_key = None
        self.account.acc_key_hash = None
        
        self._game_disconnect()
        
    def _configure(self, *args, **kwargs):
        """ Set up some configuration for the client and the requester. 
            The requester configuration is not really needed, but just incase
            it does change in the future.
        """
        config_map = {
            "chatport" : self.config,
            "protocol" : self.config,
            "invis" : self.config,
            "masterserver" : self.__requester.config,
            "basicserver" : self.__requester.config,
            "honver" : self.__requester.config
        }
        
        for kwarg in kwargs:
            if kwarg in config_map:
                config_map[kwarg][kwarg] = kwargs[kwarg]

    """ Master server related functions. """
    def _login(self, username, password):
        """ HTTP login request to the master server.
            Catches the following:
                * Failed to get login data after 3 attempts.
                * Could not connect to the masterserver.
                * Could not obtain login data
                * Incorrect username/password
        """
        attempts = 1
        while True:
            try:
                response = self.__requester.login(username, password)
                break
            except MasterServerError:
                if attempts == 3:
                    raise   # Re-raise the last exception given
                timeout = pow(2, attempts)
                time.sleep(timeout)
                attempts += 1

        if response == None:
            raise MasterServerError(100)
        elif response == "":
            raise MasterServerError(101)

        # Pass the data to the deserialiser
        try:
            self.account, new_users = deserialise.parse(response)
            self.account.logged_in = True
        except MasterServerError:
            raise MasterServerError(101)

        for user in new_users:
            self.__users[user.account_id] = user

        return True

    def _logout(self):
        """ Send a logout request to the masterserver and log out the account.
            Is forcing the logout okay? Breaking the connection to the chat server technically 
            logs the user out... What is the effect of sending the logout request to the masterserver?
            TODO: Fail cases, handle them!
                * Connection timed out
                * Connection refused.
        """
        if self.account == None:
            return
        
        if not self.account.cookie:
            self.account.logged_in = False
        else:
            attempts = 0
            while True:
                try:
                    self.__requester.logout(self.account.cookie)
                    self.account.logged_in = False
                    break
                except MasterServerError, e:
                    if attempts == 3:
                        # Force the logout and raise the error
                        self.account.logged_in = False
                        raise   # Re-raise the last exception given
                        break
                    timeout = pow(2, attempts)
                    time.sleep(timeout)
                    attempts += 1

    """ Chatserver related functions"""
    def _chat_connect(self):
        """ Sends the initial authentication request to the chatserver via the chat socket object.

            Ensures the user information required for authentication is available, otherwise raises
            a ChatServerError #205 (No cookie/auth hash provided)

            If for some reason a ChatSocket does not exist then one is created.
            Connects that chat socket to the correct address and port. Any exceptions are raised to the top method.
            Finally sends a valid authentication packet. Any exceptions are raised to the top method.
        """
        if self.account == None or self.account.cookie == None or self.account.auth_hash == None:
            raise ChatServerError(205)
       
        if self.__chat_socket is None:
            self.__chat_socket = ChatSocket(self.events)
        try:
            self.__chat_socket.connect(self.account.chat_url, self.config['chatport']) # Basic connection to the socket
        except HoNCoreError as e:
            if e.code == 10: # Socket error.
                raise ChatServerError(208) # Could not connect to the chat server.
            elif e.code == 11: # Socket timed out.
                raise ChatServerError(201)
            
        # Send initial authentication request to the chat server.
        # TODO: If the chat server did not respond to the auth request after a set number of attempts then increment the chat protocol version.
        try:
            self.__chat_socket.send_auth_info(self.account.account_id, self.account.cookie, self.account.ip, self.account.auth_hash,  self.config['protocol'], self.config['invis'])
        except ChatServerError:
            raise # Re-raise the exception.
        
        # The idea is to give 10 seconds for the chat server to respond to the authentication request.
        # If it is accepted, then the `is_authenticated` flag will be set to true.
        # NOTE: Lag will make this sort of iffy....
        attempts = 1
        while attempts is not 10:
            if self.__chat_socket.is_authenticated:
                return True
            else:
                time.sleep(1)
                attempts += 1
        raise ChatServerError(200) # Server did not respond to the authentication request 
        
    def _chat_disconnect(self):
        """ Disconnect gracefully from the chat server and close & remove the socket."""
        if self.__chat_socket is not None:
            self.__chat_socket.disconnect()
                
    """ Gameserver related functions"""
    def _game_create(self, game_name):
        """ Sends the create game request to a gameserver via the game socket object.

            Ensures the user information required is available, otherwise raises
            a GameServerError #205 (No session key/auth hash provided)

            If for some reason a GameSocket does not exist then one is created.
            Connects that game socket to the correct address and port. Any exceptions are raised to the top method.
        """
        
        if not all([self.account, self.account.cookie, self.account.auth_hash, self.account.game_ip, self.account.game_port, self.account.acc_key, self.account.acc_key_hash]):
            raise GameServerError(205)
       
        if self.__game_socket is None:
            self.__game_socket = GameSocket()
        try:
            self.__game_socket.connect(self.account.game_ip, self.account.game_port) # Basic connection to the socket
        except HoNCoreError as e:
            if e.code == 10: # Socket error.
                raise GameServerError(208) # Could not connect to the game server.
            elif e.code == 11: # Socket timed out.
                raise GameServerError(201)
        
        # Send initial authentication request to the game server.
        try:
            self.__game_socket.create_game(game_name = game_name, 
                                           player_name = self.account.nickname, 
                                          cookie = self.account.cookie,  
                                          ip = self.account.ip, 
                                          acc_key = self.account.acc_key, 
                                          account_id = self.account.account_id, 
                                          acc_key_hash = self.account.acc_key_hash, 
                                          auth_hash = self.account.auth_hash)
        except GameServerError:
            raise # Re-raise the exception.
        
        # The idea is to give 10 seconds for the chat server to respond to the authentication request.
        # If it is accepted, then the `is_authenticated` flag will be set to true.
        # NOTE: Lag will make this sort of iffy....
        attempts = 1
        while attempts is not 10:
            if self.__game_socket.is_authenticated:
                return True
            else:
                time.sleep(1)
                attempts += 1
        raise GameServerError(200) # Server did not respond to the authentication request 
        
    def _game_connect(self):
        """ Sends the join game request to a gameserver via the game socket object.

            Ensures the user information required is available, otherwise raises
            a GameServerError #205 (No session key/auth hash provided)

            If for some reason a GameSocket does not exist then one is created.
            Connects that game socket to the correct address and port. Any exceptions are raised to the top method.
        """
        
        if not all([self.account, self.account.cookie, self.account.auth_hash, self.account.game_ip, self.account.game_port]):
            raise GameServerError(205)
       
        if self.__game_socket is None:
            self.__game_socket = GameSocket()
        try:
            self.__game_socket.connect(self.account.game_ip, self.account.game_port) # Basic connection to the socket
        except HoNCoreError as e:
            if e.code == 10: # Socket error.
                raise GameServerError(208) # Could not connect to the game server.
            elif e.code == 11: # Socket timed out.
                raise GameServerError(201)
        
        # Send initial authentication request to the game server.
        try:
            self.__game_socket.join_game(player_name = self.account.nickname, 
                                          cookie = self.account.cookie,  
                                          ip = self.account.ip, 
                                          account_id = self.account.account_id, 
                                          auth_hash = self.account.auth_hash)
            #self.__game_socket.send_magic_packet()
        except GameServerError:
            raise # Re-raise the exception.
        
        # The idea is to give 10 seconds for the chat server to respond to the authentication request.
        # If it is accepted, then the `is_authenticated` flag will be set to true.
        # NOTE: Lag will make this sort of iffy....
        attempts = 1
        while attempts is not 10:
            if self.__game_socket.is_authenticated:
                return True
            else:
                time.sleep(1)
                attempts += 1
        raise GameServerError(200) # Server did not respond to the authentication request 
        
    def _game_disconnect(self):
        """ Disconnect gracefully from the game server and close & remove the socket."""
        if self.__game_socket is not None:
            self.__game_socket.disconnect()

    @property
    def is_logged_in(self):
        """
        Override this and provide a way to handle being logged in.
        """
        pass

    @property
    def is_connected(self):
        """ Test for chat server connection. 

            The line of thought here is, the client can not be connected to the chat server
            until it is authenticated, the chat socket can be connected as long as the server
            doesn't deny or drop the connection.

            Once a user is logged in to a HoN client, they can be logged in but not connected.

            This would happen if a chat server connection is dropped unexpectedly or is never initialised.
            The main program would use this to check for that and then handle it itself.
        """
        # Check the socket exists.
        if self.__chat_socket is None:
            return False
        # Ensure the user is authenticated against the chat server
        if self.__chat_socket.is_authenticated is False:
            return False
        # Check the status of the chat socket object.
        if self.__chat_socket.is_connected is False:
            return False
        # Any other checks to be done..? 
        return True
        
    @property
    def is_connected_to_game(self):
        """ Test for game server connection. 

            The line of thought here is, the client can not be connected to the game server
            until it is authenticated, the game socket can be connected as long as the server
            doesn't deny or drop the connection.
        """
        # Check the socket exists.
        if self.__game_socket is None:
            return False
        # Ensure the user is authenticated against the game server
        if self.__game_socket.is_authenticated is False:
            return False
        # Check the status of the game socket object.
        if self.__game_socket.is_connected is False:
            return False
        # Any other checks to be done..? 
        return True

    """ Message of the day related functions"""
    def motd_get(self):
        """ Requests the message of the day entries from the server and then pushes them through motd_parse.
            Returns a dict of motd entries.
        """
        raw = self.__requester.motd()
        try:
            raw = deserialise.parse_raw(raw)
        except ValueError:
            raise MasterServerError(108)
        return self.__motd_parse(raw)

    def __motd_parse(self, raw):
        """ Parses the message of the day entries into a dictionary of the format:
            motd = {
                motd_list = [
                    {
                        ["title"] = "Item 1 title",
                        ["author"] = "MsPudding",
                        ["date"] = "6/30/2011"
                        ["body"] = "This is the body of the message including line feeds"
                    },
                    {
                        ["title"] = "Item 2 title", 
                        ["author"] = "Konrar",
                        ["date"] = "6/29/2011",
                        ["body"] = "This is the body text Sometimes there are ^rColours^*"
                    }
                ],
                image = "http://icb.s2games.com/motd/4e67cffcc959e.jpg",
                server_data = "We are aware of the server issues....",
                honcast = 0
            }
            The first index will always be the newest....... Right?
        """
        motd = {'motd_list': [], 'image': '', 'server_data': '', 'honcast': 0}
        # Split the full string into a list of entries.
        for entry in raw['motddata'].split("|"):
            #try:
            title, body, author, date = entry.split("`")
            motd['motd_list'].append({"title" : title, "author" : author, "date" : date, "body" : body})
            #except ValueError:
                #raise MasterServerError(113) # Motd data error
        motd['image'] = raw['motdimg']
        motd['server_data'] = raw['serverdata']
        motd['honcast'] = raw['honcast']
        return motd
        
    """ Server list related functions"""
    def server_list_get(self):
        """ Requests the server list from the server and then pushes them through __server_list_parse.
            Returns a dict of server ips.
        """
        print 'Getting servers list...'
        raw = self.__requester.server_list(self.account.cookie, GAME_SERVER_TYPE)
        try:
            raw = deserialise.parse_raw(raw)
        except ValueError:
            raise MasterServerError(108)
        return self.__server_list_parse(raw)

    def __server_list_parse(self, raw):
        """ Parses the server_list into a dictionary of the format:
            servers_dict = {
                server_list : {
                    server_id : {
                        ["ip"] = Game Server 1 IP Address,
                        ["server_id"] = Game Server 1 ID,
                        ["session"] = Game Server 1 Session Key,
                        ["port"] = Game Server 1 Connection Port,
                        ["location"] = Game Server 1 Location
                    },
                    server_id : {
                        ["ip"] = Game Server 2 IP Address,
                        ["server_id"] = Game Server 2 ID,
                        ["session"] = Game Server 2 Session Key,
                        ["port"] = Game Server 2 Connection Port,
                        ["location"] = Game Server 2 Location
                    },
                    ....
                },
                acc_key = User Account Key,
                acc_key_hash = User Account Key Hash
            }
        """
        servers_dict = {}
        servers_dict['server_list'] = raw['server_list']
        servers_dict['acc_key'] = raw['acc_key']
        servers_dict['acc_key_hash'] = raw['acc_key_hash']
        
        return servers_dict

    """ The core client functions."""
    def send_channel_message(self, message, channel_id):
        """ Sends a message to a specified channel.
            Takes 2 parameters.
                `message`   The message to be send.
                `channel_id`   The id of the channel to send it to.
        """
        # TODO: Implement throttling for messages.
        self.__chat_socket.send_channel_message(message, channel_id)

    def join_channel(self, channel, password=None):
        """ Sends a request to join a channel.
            
            Takes 2 paramters.
                `channel`   A string containing the channel name.
                `password`  The optional password required to join the channel.
        """
        if password:
            self.__chat_socket.send_join_channel_password(channel, password)
        elif not password:
            self.__chat_socket.send_join_channel(channel)

    def send_whisper(self, player, message):
        """ Sends the message to the player.
            Takes 2 parameters.
                `player`    A string containing the player's name.
                `message`   A string containing the message.
        """
        self.__chat_socket.send_whisper(player, message)

    def send_private_message(self, player, message):
        """ Sends the message to the player.
            Takes 2 parameters.
                `player`    A string containing the player's name.
                `message`   A string containing the message.
        """
        self.__chat_socket.send_private_message(player, message)
        
    def send_buddy_add_notify(self, player):
        """ Send a buddy add notify to the player.
            Takes 1 parameter.
                `player`    A string containing the player's name.
        """
        self.__chat_socket.send_buddy_add_notify(player)
        
    def send_buddy_accept(self, player):
        """ Sends a  buddy accept.
            Takes 1 parameter.
                `player`    A string containing the player's name.
        """
        self.__chat_socket.send_buddy_accept(player)
    
    def send_join_game(self, game_server_ip):
        """ Sends a join game notification.
            Takes 1 parameter.
                `game_server_ip`  A string containing the game server IP.
        """
        self.__chat_socket.send_join_game(game_server_ip)
        
    def send_game_invite(self, player):
        """ Sends a game invite to the player.
            Takes 1 parameter.
                `player`    A string containing the player's name.
        """
        self.__game_socket.send_game_invite(player)
        self.__chat_socket.send_game_invite(player)
        
    def send_mass_invite(self, channel_name):
        """ Sends a game invite to all the players of a channel.
            Takes 1 parameter.
                `channel_name`    A string containing the channel name.
        """
        channel = self.name_to_channel(channel_name)
        for player in channel.users:
            print 'Sending invite to player : %s'%player
            self.__game_socket.send_game_invite(player.nickname)
            self.__chat_socket.send_game_invite(player.nickname)

    def send_game_server_ip(self, server_ip):
        """ Sends a chosen game server ip to the chat server.
            Takes 1 parameter.
                `server_ip`    A string containing the chosen server IP
        """
        self.__chat_socket.send_game_server_ip(server_ip)

    def create_game(self, game_name):
        """ Create the game with the given name.
            Takes 1 parameter.
                'game_name' A string containing the game name.
        """
        server_infos = self.pick_game_server(MAXIMUM_SERVER_PING)
        # Save game server infos into account
        self.account.game_session_key = server_infos['server_info']['session']
        self.account.game_ip = server_infos['server_info']['ip']
        self.account.game_port = int(server_infos['server_info']['port'])
        self.account.game_host_id = server_infos['server_info']['server_id']
        self.account.acc_key = server_infos['acc_key']
        self.account.acc_key_hash = server_infos['acc_key_hash']
        self.send_join_game(self.account.game_ip)
        self._game_create(game_name)
        
    def pick_game_server(self, maximum_ping=150):
        """ Request masterserver for server list, and return the first game server infos with a ping under
            the maximum ping given, along with acc_key and acc_key_hash
        """
        pinged_servers = []
        servers_dict = self.server_list_get()
        
        for gameserver_id, gameserver_info in servers_dict['server_list'].items():
            if 'ip' in gameserver_info and gameserver_info['ip'] not in pinged_servers:
                pinged_servers.append(gameserver_info['ip'])
                try:
                    server_ping = ping_server(gameserver_info['ip'])
                    if 0 < server_ping < maximum_ping:
                        return {'server_info' : gameserver_info, 
                                'acc_key' : servers_dict['acc_key'], 
                                'acc_key_hash' : servers_dict['acc_key_hash']
                                }
                except:
                    continue
        return -1

    """ Utility functions """
    def connect_event(self, event_id, method, priority=5):
        """ Wrapper method for connecting events. """
        try:
            self.__events[event_id].connect(method, priority)
        except KeyError:
            try:
                self.__game_events[event_id].connect(method, priority)
            except:
                raise HoNCoreError(13) # Unknown event ID 
    
    def disconnect_event(self, event_id, method):
        """ Wrapper method for disconnecting events. """
        try:
            self.__events[event_id].disconnect(method)
        except HoNCoreError, e:
            if e.id == 14: # Method is not connected to this event.
                raise
        except KeyError:
            try:
                self.__game_events[event_id].disconnect(method)
            except:
                raise HoNCoreError(13) # Unknown event ID
                
    def connect_game_event(self, event_id, method, priority=5):
        """ Wrapper method for connecting events. """
        try:
            self.__game_events[event_id].connect(method, priority)
        except :
            raise HoNCoreError(13) # Unknown event ID 
                
    def disconnect_game_event(self, event_id, method):
        """ Wrapper method for disconnecting events. """
        try:
            self.__game_events[event_id].disconnect(method)
        except HoNCoreError, e:
            if e.id == 14: # Method is not connected to this event.
                raise
        except:
            raise HoNCoreError(13) # Unknown event ID

    def id_to_channel(self, channel_id):
        """ Wrapper function to return the channel name for the given ID.
            If no channel was found then return None
        """
        try:
            return self.__channels[channel_id]
        except KeyError:
            return None

    def id_to_nick(self, account_id):
        """ Wrapper function to return the nickname for the user associated with that account ID.
            If no nickname was found then return None
        """
        try:
            return self.__users[account_id].nickname
        except KeyError:
            return None

    def id_to_user(self, account_id):
        """ Wrapper function to return the user object for the user associated with that account ID.
            If no user was found then return None
        """
        try:
            return self.__users[account_id]
        except KeyError:
            return None

    def name_to_channel(self, channel_name):
        """ Wrapper function to return the channel object for the channel associated with that channel_name.
            If no channel was found then return None
        """
        for channel_id, channel in self.__channels.items():
            if channel.name == channel_name:
                return channel
        
        return None

    def get_buddies(self):
        buddies = []
        for buddy_id in self.account.buddy_list:
            buddies.append(self.__users[buddy_id])
        return buddies

    """ Debugging functions """
    def list_users(self):
        for aid in self.__users:
            print self.__users[aid]

class Event:
    """ Event objects represent network level events which can have functions connected to them, which
        are then triggered when the event occurs.

        A standard set of events are initialised by the library which should cover nearly everything.
        The core client will store a list of the standard events in client.events.

        The front end client should then connect these events to functions by calling the connect 
        method on the specific event object. e.g.

        self.events.login.connect(self.on_login_event)

        The functions are stored in a list called handlers, each function is ran when the event is triggered.

        The functions can be assigned a priority so that they are executed in an order. This is useful for
        ensuring that lower level network/core client related functions are executed first.

        On the networking side, the events are triggered after the packet data has been parsed and constructed into useful data.
        The process would be as follows:
        
            packet = sock.recv(512)
            id = parse_id(packet)
            useful_data = raw_parse(id, packet)
            event.trigger(useful_data)
    """

    class ConnectedMethod:
        def __init__(self, method, priority):
            self.method = method
            self.priority = priority
        
        def __repr__(self):
            return "[%s %s]" % (self.method, self.priority)

    def __init__(self, name, packet_id):
        self.name = name            # An english, human name for the event. Maybe it can be used for a lookup later. Not sure of a use for it right now.
        self.packet_id = packet_id  # A packet identifier, either a constant or a hex value of a packet. i.e HON_SC_TOTAL_ONLINE or 0x68.
        self.handlers = []          # List of connected methods.
    
    def __repr__(self):
        return "<%s: %s>" % (self.packet_id, self.name)
    
    def connect(self, function, priority=5):
        """ Connects a function to a specific event.
            The event is given as an english name, which corresponds
            to a constant in the packet definition file.
        """
        self.handlers.append(self.ConnectedMethod(function, priority))

    def disconnect(self, method):
        """ Hopefully it can be used to remove event handlers from this event
            object so they are no longer triggered. Useful if say, an event only 
            needs to be triggered once, for a reminder or such.
        """
        for cm in self.handlers:
            if cm.method == method:
                self.handlers.remove(cm)
            else:
                raise HoNCoreError(14) # Method is not connected to this event_id
        pass
    
    def trigger(self, **data):
        """ Sorts the connected handlers based on their priority and calls each one in turn,
            passing the dictionary of keyword arguments, or alternatively with no arguments.
        """
        for cm in sorted(self.handlers, key=lambda c: c.priority):
            f = cm.method
            num_args = f.func_code.co_argcount
            f(**data) if num_args > 0 else f()

