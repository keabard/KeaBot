""" 
HoNCore. Python library providing connectivity and functionality
with HoN's chat server.
"""

import struct, time, threading, thread, socket
from exceptions import *
from constants import *
from common import User
from lib.construct import *
from utils import *


class SocketListener(threading.Thread):
    """ A threaded listener class. Enables the receiving and parsing
        of packets to be done in the background.
        Receives the packet and in an addition thread parses the packet
        and triggers any event handlers.
    """
    def __init__(self, socket):
        threading.Thread.__init__(self, name='SocketListener')
        self.socket = socket
        self.stopped = False

    def __repr__(self):
        return "<SocketListener on socket %s>" % self.socket.socket

    def run(self):
        while not self.stopped:
            try:
                packet = self.socket.recv()
                if not packet:
                    #print "Empty packet received, socket terminated."
                    self.stopped = True
                    break
                #print "Packet 0x%x on socket %s" % (struct.unpack('H', packet[2:4])[0], self.socket.socket)
                threading.Thread(target=self.socket.parse_packet, name='PacketParser', args=(packet,)).start()
                #self.socket.parse_packet(packet)
            except socket.timeout, e:
                #print "Socket.timeout: %s" % e
                continue
            except socket.error, e:
                #print "Socket.error: %s" % e
                break
                
class SocketSender(threading.Thread):
    """ A threaded sender class. Enables the sending
        of packets to be done in the background, periodically.
    """
    def __init__(self, socket, period, packet):
        threading.Thread.__init__(self, name='SocketSender')
        self.socket = socket
        self.period = period
        self.packet = packet
        self.stopped = False
        

    def __repr__(self):
        return "<SocketSender on socket %s>" % self.socket

    def run(self):
        while not self.stopped:
            try:
                self.socket.send(self.packet)
                time.sleep(self.period)
                #print "Packet %s sent on socket %s" % (self.packet, self.socket)
            except socket.timeout, e:
                #print "Socket.timeout: %s" % e
                continue
            except socket.error, e:
                #print "Socket.error: %s" % e
                break

class ChatSocket:
    """ Represents the socket connected to the chat server.
        This object will be created once with the client, and only one will 
        be maintained, however the socket and listener will be re-created for
        each connection used. GC should pick up the old and unused ones.

        The ChatSocket holds two state flags.
            `connected` Represents the state of the actual socket.
            `authenticated` Represents the state of the chat server, and if it 
                            is happy to communicate.
        Both states are used to consider if the connection is available.
    """
    def __init__(self, client_events):
        self.socket = None
        self.connected = False
        self.authenticated = False
        self.listener = None
        self.packet_parser = PacketParser()
        self.events = client_events

        # Transparently connect the ping event to the pong sender.
        self.events[HON_SC_PING].connect(self.send_pong, priority=1)

        # Some internal handling of the authentication process is also needed
        self.events[HON_SC_AUTH_ACCEPTED].connect(self.on_auth_accepted, priority=1)

    @property
    def is_authenticated(self):
        """ The ChatSocket becomes authenticated with the Chat Server once
            the `auth_accepted` packet has been received. The ChatSocket will
            then be authenticated until the connection is lost.
        """
        return self.authenticated

    @property
    def is_connected(self): 
        """ Upon forgetting to connect the ping handler, it is possible to
            see the effects of a ping timeout. This means the socket can be terminated
            early by the server, but at this time it will just leave the program
            hanging. This is_connected method needs to be expanded to check for ping
            timeouts possibly, or simply if the socket is still connected or the 
            socket listener is still running.

            28.10.11 -- There seems to be a bug with HoN's chat server. 
            When two clients wish to connect to the chat server with the same
            credentials, one will get the connection, while the other will enter
            a strange loop of "Connecting.... Disconnected.." 
            The same can be seen using the S2 client and my own client.
            The behaviour I remember and would expect is that, since each client's reconnect
            cycle is staggered by 30 seconds, the connection would ping-poing between the two
            clients every 30 seconds. Each client would hold the connection for 30 seconds.
        """
        # The socket has been broken early.
        if self.listener.stopped is True and self.connected is True:
            self.connected = False
            self.authenticated = False
        return self.connected

    def connect(self, address, port):
        """ Creates a connection to the chat server and starts listening for packets.
            At the moment it is VITAL that the authentication is sent within a second or two
            of connecting to the socket, otherwise it will simply hang up.
            But this is done anyway, in the client's connect event.
        """
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            #self.socket.bind(("", 0))
            self.socket.connect((address, port))
        except socket.timeout:
            raise HoNCoreError(11)
        except socket.error, e:
            if e.errno == 110:
                raise HoNCoreError(11) # Socket timed out
            raise HoNCoreError(10) # Socket error
        
        # The socket is now actually connected.
        self.connected = True

        # Set up a listener as the socket can send data now.
        self.listener = SocketListener(self)
        self.listener.start()

    def disconnect(self):
        """ Disconnecting should not fail, it's a pretty forced procedure.
            Set the internal state of the socket to be disabled, and set
            authenticated to False.
            Clear the socket object and listener so they can be dereferenced.
        """

        self.connected = False
        self.authenticated = False

        try:
            self.socket.shutdown(socket.SHUT_RDWR)
            self.socket.close()
        except socket.error, e:
            raise HoNCoreError(10) # Socket Error
        finally:
            self.socket = None

        self.listener.stopped = True
        self.listener.join()
        self.listener = None
    
    def send(self, data):
        """ Wrapper send method. 
            TODO: Capture failed sends.
            TODO: Possibly check for the authentication first, and authenticate if required.
        """
        #print "Sending on socket %s from thread %s" % (self.socket, threading.currentThread().getName())
        try:
            self.socket.send(data)
        except socket.error, e:
            #print "Socket error %s while sending." % e
            raise
        return True

    def recv(self):
        # Packet length is packed into 2 bytes at the start.
        packet_len = self.socket.recv(2)
        if not packet_len:
            return
        # Add the first two bytes to the packet
        packet = packet_len
        # Extract the packet length.
        packet_len = struct.unpack('>H', packet_len)[0]

        # Receive until the entire packet has been received.
        received_len = to_get = 0
        while received_len < packet_len:
            to_get = packet_len - received_len
            packet += self.socket.recv(to_get)
            received_len = len(packet) - 2
        
        return packet
    
    def parse_packet(self, packet):
        """ Core function to tie together all of the packet parsing. """
        packet_id = self.packet_parser.parse_id(packet)
        
        # Trigger a general event on all packets. Passes the raw packet.
        try:
            self.events[HON_SC_PACKET_RECV].trigger(**{'packet_id': packet_id, 'packet': packet})
        except KeyError:
            pass
        
        # Trim the length and packet id from the packet
        packet = packet[4:]
        try:
            packet_data = self.packet_parser.parse_data(packet_id, packet)
        except HoNCoreError, e:
            if e.code == 12: # Unknown packet received.    
                return False

        if packet_data is None:
            return
        
        if packet_id in self.events:
            event = self.events[packet_id]
            event.trigger(**packet_data)

    def on_auth_accepted(self, *p):
        """ Set the authenticated state to True"""
        self.authenticated = True

    def send_pong(self):
        self.send(struct.pack('H', HON_CS_PONG))
    
    def send_channel_message(self, message, channel_id):
        """ Sends the messae to the channel specified by the id.
            Takes 2 parameters.
                `message`       A string containing the message.
                `channel_id`    An integer containing the id of the channel.
            Packet ID is 0x03 or HON_CS_CHANNEL_MSG.
        """
        c = Struct("message",
                ULInt16("id"),
                String("message", len(message)+1, encoding="utf8", padchar="\x00"),
                ULInt32("channel_id")
            )
        packet = c.build(Container(id=HON_CS_CHANNEL_MSG, message=unicode(message), channel_id=channel_id))
        self.send(packet)
    
    def send_whisper(self, player, message):
        """ Sends the message to the player in the form of a whisper.
            Takes 2 parameters.
                `player`    A string containing the player's name.
                `message`   A string containing the message.
            Packet ID is 0x08 or HON_CS_WHISPER.
        """

        c = Struct("whisper",
               ULInt16("id"),
               String("player", len(player)+1, encoding="utf8", padchar="\x00"),
               String("message", len(message)+1, encoding="utf8", padchar="\x00")
        )
        packet = c.build(Container(id=HON_CS_WHISPER, player=unicode(player), message=unicode(message)))
        self.send(packet)

    def send_auth_info(self, account_id, cookie, ip, auth_hash, protocol, invis):
        """ Sends the chat server authentication request.
            Takes 6 parameters.
                `account_id`    An integer containing the player's account ID.
                `cookie`        A 33 character string containing a cookie.
                `ip`            A string containing the player's IP address.
                `auth`          A string containing an authentication hash.
                `protocol`      An integer containing the protocol version to be used.
                `invis`         A boolean value, determening if invisible mode is used.
        """
        c = Struct("login",
                ULInt16("id"),
                ULInt32("aid"),
                String("cookie", len(cookie)+1, encoding="utf8", padchar = "\x00"),
                String("ip", len(ip)+1, encoding="utf8", padchar = "\x00"),
                String("auth", len(auth_hash)+1, encoding="utf8", padchar = "\x00"),
                ULInt32("proto"),
                ULInt8("unknown"),
                ULInt32("mode")
        )

        packet = c.build(Container(id=HON_CS_AUTH_INFO, aid=account_id, cookie=unicode(cookie), ip=unicode(ip), 
                                auth=unicode(auth_hash), proto=protocol, unknown=0x01, mode=0x03 if invis else 0x00))
        
        # print "Sending packet - 0x%x:%s:%s:%s:%s:0x%x:0x%x:0x%x" % (HON_CS_AUTH_INFO, account_id, cookie, ip, auth_hash, protocol, 0x01, 0x00)
        try:
            self.send(packet)
        except socket.error, e:
            if e.errno == 32:
                raise ChatServerError(206)

    def send_buddy_add_notify(self, player):
        """ Send a buddy invite to the player
            Packet ID : 0x0D
        """
        c = Struct("buddy_invite", 
                   ULInt16("id"), 
                   String("player",  len(player)+1, encoding="utf8", padchar="\x00"))
        packet = c.build(Container(id=HON_CS_BUDDY_ADD_NOTIFY, player=unicode(player)))
        print 'Sending buddy add notify to player : %s'%player
        self.send(packet)

    def send_join_game(self, game_server_ip):
        """ Send a 'join game' notification to the server 
            Packet ID : 0x10
        """
    
        # Send game server IP
        
        c = Struct("send_server_ip", 
                   ULInt16("id"), 
                   String("server_ip",  len(game_server_ip)+1, encoding="utf8", padchar="\x00"))
      
        packet = c.build(Container(
                                   id=HON_CS_GAME_SERVER_IP, 
                                   server_ip=unicode(game_server_ip)))
                                   
        self.send(packet)
        
#        # Send game name packet
#        c = Struct("join_game", 
#                   ULInt16("id"), 
#                   String("game_name",  len(game_name)+1, encoding="utf8", padchar="\x00"), 
#                   ULInt32("magic_int"), 
#                   Byte("magic_byte"))
#                   
#        packet = c.build(Container(
#                                   id=HON_CS_JOIN_GAME, 
#                                   game_name=unicode(game_name), 
#                                   magic_int = 87819808,
#                                   magic_byte = 1))
#                                   
#        self.send(packet)
    
    def send_clan_message(self):
        pass

    def send_private_message(self, player, message):
        """ Sends the message to the player in the form of a private message.
            Packet ID: 0x1C
        """
        c = Struct("private_message",
               ULInt16("id"),
               String("player", len(player)+1, encoding="utf8", padchar="\x00"),
               String("message", len(message)+1, encoding="utf8", padchar="\x00")
            )
        packet = c.build(Container(id=HON_CS_PM, player=unicode(player), message=unicode(message)))
        self.send(packet)


    def send_join_channel(self, channel):
        """ Sends a request to join the channel.
            Packet ID: 0x1E
        """
        c = Struct("join_channel",
                ULInt16("id"),
                String("channel", len(channel)+1, encoding="utf8", padchar="\x00")
            )
        packet = c.build(Container(id=HON_CS_JOIN_CHANNEL, channel=unicode(channel)))
        self.send(packet)

    def send_whisper_buddies(self):
        pass

    def send_leave_channel(self, channel):
        """ Leaves the channel `channel`.
            Packet ID: 0x22 
        """
        c = Struct("leave_channel",
               ULInt16("id"),
               String("channel", len(channel)+1, encoding="utf8", padchar="\x00")
            )
        packet = c.build(Container(id=HON_CS_LEAVE_CHANNEL, channel=unicode(channel)))
        self.send(packet)

    def send_user_info(self):
        pass

    def send_update_topic(self):
        pass

    def send_channel_kick(self):
        pass

    def send_channel_ban(self):
        pass
    
    def send_channel_unban(self):
        pass

    def send_channel_silence_user(self):
        pass

    def send_channel_promote(self):
        pass

    def send_channel_demote(self):
        pass

    def send_channel_auth_enable(self):
        pass

    def send_channel_auth_disable(self):
        pass

    def send_channel_auth_add(self):
        pass

    def send_channel_auth_delete(self):
        pass

    def send_channel_auth_list(self):
        pass

    def send_join_channel_password(self, channel, password):
        pass

    def send_clan_add_member(self):
        pass

    def send_channel_emote(self):
        pass

    def send_buddy_accept(self,  player):
        """ Sends a positive buddy request response.
            Packet ID: 0xB3
        """
        c = Struct("buddy_accept", 
                   ULInt16("id"),
                   String("player",  len(player)+1,  encoding="utf8",  padchar="\x00")
                   )
        packet = c.build(Container(id=HON_CS_BUDDY_ACCEPT, player=unicode(player)))
        self.send(packet)

    def send_game_invite(self, player):
        """ Sends a request to join the current game.
            Packet ID: 0x24
        """
        c = Struct("game_invite",
                ULInt16("id"),
                String("player", len(player)+1, encoding="utf8", padchar="\x00")
            )
        packet = c.build(Container(id=HON_CS_GAME_INVITE, player=unicode(player)))
        self.send(packet)
    
class PacketParser:
    """ A class to handle raw packet parsing. """
    def __init__(self):
        self.__packet_parsers = {}
        self.__setup_parsers()

    def __setup_parsers(self):
        """ Add every known packet parser to the list of availble parsers. """
        self.__add_parser(HON_SC_AUTH_ACCEPTED, self.parse_auth_accepted)
        self.__add_parser(HON_SC_PING, self.parse_ping)
        self.__add_parser(HON_SC_CHANNEL_MSG, self.parse_channel_message)
        self.__add_parser(HON_SC_JOINED_CHANNEL, self.parse_joined_channel)
        self.__add_parser(HON_SC_ENTERED_CHANNEL, self.parse_entered_channel)
        self.__add_parser(HON_SC_LEFT_CHANNEL, self.parse_left_channel)
        self.__add_parser(HON_SC_WHISPER, self.parse_whisper)
        self.__add_parser(HON_SC_WHISPER_FAILED, self.parse_whisper_failed)
        self.__add_parser(HON_SC_INITIAL_STATUS, self.parse_initial_status)
        self.__add_parser(HON_SC_UPDATE_STATUS, self.parse_update_status)
        self.__add_parser(HON_SC_CLAN_MESSAGE, self.parse_clan_message)
        self.__add_parser(HON_SC_LOOKING_FOR_CLAN, self.parse_looking_for_clan)
        self.__add_parser(HON_SC_PM, self.parse_private_message)
        self.__add_parser(HON_SC_PM_FAILED, self.parse_private_message_failed)
        self.__add_parser(HON_SC_WHISPER_BUDDIES, self.parse_whisper_buddies)
        self.__add_parser(HON_SC_MAX_CHANNELS, self.parse_max_channels)
        self.__add_parser(HON_SC_USER_INFO_NO_EXIST, self.parse_user_info_no_exist)
        self.__add_parser(HON_SC_USER_INFO_OFFLINE, self.parse_user_info_offline)
        self.__add_parser(HON_SC_USER_INFO_ONLINE, self.parse_user_info_online)
        self.__add_parser(HON_SC_USER_INFO_IN_GAME, self.parse_user_info_ingame)
        self.__add_parser(HON_SC_CHANNEL_UPDATE, self.parse_channel_update)
        self.__add_parser(HON_SC_CHANNEL_UPDATE_TOPIC, self.parse_channel_update_topic)
        self.__add_parser(HON_SC_CHANNEL_KICK, self.parse_channel_kick)
        self.__add_parser(HON_SC_CHANNEL_BAN, self.parse_channel_ban)
        self.__add_parser(HON_SC_CHANNEL_UNBAN, self.parse_channel_unban)
        self.__add_parser(HON_SC_CHANNEL_BANNED, self.parse_channel_banned)
        self.__add_parser(HON_SC_CHANNEL_SILENCED, self.parse_channel_silenced)
        self.__add_parser(HON_SC_CHANNEL_SILENCE_LIFTED, self.parse_channel_silence_lifted)
        self.__add_parser(HON_SC_CHANNEL_SILENCE_PLACED, self.parse_channel_silence_placed)
        self.__add_parser(HON_SC_MESSAGE_ALL, self.parse_message_all)
        self.__add_parser(HON_SC_CHANNEL_PROMOTE, self.parse_channel_promote)
        self.__add_parser(HON_SC_CHANNEL_DEMOTE, self.parse_channel_demote)
        self.__add_parser(HON_SC_CHANNEL_AUTH_ENABLE, self.parse_channel_auth_enable)
        self.__add_parser(HON_SC_CHANNEL_AUTH_DISABLE, self.parse_channel_auth_disable)
        self.__add_parser(HON_SC_CHANNEL_AUTH_ADD, self.parse_channel_auth_add)
        self.__add_parser(HON_SC_CHANNEL_AUTH_DELETE, self.parse_channel_auth_delete)
        self.__add_parser(HON_SC_CHANNEL_AUTH_LIST, self.parse_channel_auth_list)
        self.__add_parser(HON_SC_CHANNEL_PASSWORD_CHANGED, self.parse_channel_password_changed)
        self.__add_parser(HON_SC_CHANNEL_AUTH_ADD_FAIL, self.parse_channel_auth_add_fail)
        self.__add_parser(HON_SC_CHANNEL_AUTH_DEL_FAIL, self.parse_channel_auth_del_fail)
        self.__add_parser(HON_SC_JOIN_CHANNEL_PASSWORD, self.parse_join_channel_password)
        self.__add_parser(HON_SC_CHANNEL_EMOTE, self.parse_channel_emote)
        self.__add_parser(HON_SC_TOTAL_ONLINE, self.parse_total_online)
        self.__add_parser(HON_SC_REQUEST_NOTIFICATION, self.parse_request_notification)
        self.__add_parser(HON_SC_NOTIFICATION, self.parse_notification)
        self.__add_parser(HON_SC_GAME_INVITE, self.parse_game_invite)
    
    def __add_parser(self, packet_id, function):
        """ Registers a parser function for the specified packet. 
            Ensures that only one parser exists for each packet.
        """
        if packet_id in self.__packet_parsers:
            return False
        self.__packet_parsers[packet_id] = function

    def parse_id(self, packet):
        """ Returns the packet's ID.
            The ID is an unsigned short, or a 2 byte integer, which is located at bytes 3 and 4 
            within the packet.
        """
        return struct.unpack('H', packet[2:4])[0]

    def parse_data(self, packet_id, packet):
        """ Pushes the packet through to a matching registered packet parser, which extracts any useful data 
            into a dict of kwargs which can then be handed to any matching registered event handler.

            Passes the packet to a packet parser so it can be parsed for data. The returned data
            is then passed to each event handler that requests it as a list of named keywords which
            are taken as arguments.
        """
        if packet_id in self.__packet_parsers:
            parser = self.__packet_parsers[packet_id]
            data = parser(packet)
            return data
        else:
            raise HoNCoreError(12) # Unknown packet received.
    
    def parse_auth_accepted(self, packet):
        """ The initial response from the chat server to verify that the authentication was accepted.
            Packet ID: 0x1C00
        """
        return {}

    def parse_ping(self, packet):
        """ Pings sent every minute. Respond with pong. 
            Packet ID: 0x2A00
        """
        return {}

    def parse_channel_message(self, packet):
        """ Triggered when a message is sent to a channel that the user is currently in.
            Returns the following:
                `account_id`    The ID of player account who sent the message.
                `channel_id`    The ID of the channel message was sent to.
                `message`       The message sent.
            Packet ID: 0x03
        """
        c = Struct('channel_message',
                   ULInt32('account_id'),
                   ULInt32('channel_id'),
                   CString('message')
                  )
        r = c.parse(packet)
        return {
            'account_id': r.account_id,
            'channel_id': r.channel_id,
            'message'   : r.message
        }
    
    def parse_joined_channel(self, packet):
        """ Triggered when `the user` joins a channel.
            Returns the following:
                `channel`       Name of the channel joined.
                `channel_id`    The ID of the channel joined.
                `topic`         The topic set for the channel.
                `operators`     A list of operators in the channel and the data regarding them.
                `users`         A list of users in the channel and data regarding them.
            Packet ID: 0x04
        """
        c = Struct('changed_channel', 
                CString('channel_name'), 
                ULInt32('channel_id'), 
                ULInt8('unknown'), 
                CString('channel_topic'), 
                ULInt32('op_count'),
                MetaRepeater(lambda ctx: ctx['op_count'],
                    Struct('op_users',
                        ULInt32('op_aid'),
                        Byte('op_type')
                    )
                ),
                ULInt32('user_count'),
                MetaRepeater(lambda ctx: ctx['user_count'],
                    Struct('users',
                        CString('nickname'),
                        ULInt32('id'),
                        Byte('status'),
                        Byte('flags'),
                        CString('chat_icon'),
                        CString('nick_colour'),
                        CString('account_icon')
                    )
                )
            )
        r = c.parse(packet)
         
        return {
            'channel': r.channel_name,
            'channel_id': r.channel_id,
            'topic': r.channel_topic,
            'operators': [op for op in r.op_users],
            'users': [User(u.id, u.nickname, u.status, u.flags, u.chat_icon,
                           u.nick_colour, u.account_icon) for u in r.users],
        }

    def parse_entered_channel(self, packet):
        """ When another user joins a channel.
            Returns the following:
                `channel_id`    The ID of the channel that the user joined.
                `user`          A `User` object containing the user that joined.
            Packet ID: 0x05
        """
        c = Struct('entered_channel',
                CString('u_nickname'),
                ULInt32('u_id'),
                ULInt32('channel_id'),
                Byte('u_status'),
                Byte('u_flags'),
                CString('u_chat_icon'),
                CString('u_nick_colour'),
                CString('u_account_icon')
            )
        r = c.parse(packet)
        u = User(r.u_id, r.u_nickname, r.u_status, r.u_flags, r.u_chat_icon,
                      r.u_nick_colour, r.u_account_icon)
        return {'channel_id': r.channel_id, 'user': u}

    def parse_left_channel(self, packet):
        pass

    def parse_whisper(self, packet):
        """ A normal whisper from anyone.
            Returns two variables.
                `player`    The name of the player who sent the whisper.
                `message`   The full message sent in the whisper
            Packet ID: 0x08
        """
        c = Struct("packet", CString("name"), CString("message"))
        r = c.parse(packet)
        return {"player" : r.name, "message" : r.message }

    def parse_whisper_failed(self, packet):
        pass

    def parse_initial_status(self, packet):
        """ The initial status packet contains information for all available buddy and clan members.
            Returns a list of dictonaries containing the user id and a dictonary with their status
            and flags.
            `users` {
                id: {
                    status
                    flag
                }
            }
            Packet ID: 0x0B
        """
        c = Struct('initial_status',
                ULInt32('user_count'),
                MetaRepeater(lambda ctx: ctx['user_count'],
                    Struct('users',
                        ULInt32('id'),
                        Byte('status'),
                        Byte('flags'),
                        If(lambda ctx: ctx['status'] == HON_STATUS_INGAME or ctx['status'] == HON_STATUS_INLOBBY,
                           Struct('match_info',
                               CString('server'),
                               CString('game_name')
                            )
                        )
                    )
                )
            )
        r = c.parse(packet)
        users = [{u.id: {'status': u.status, 'flags': u.flags}} for u in r.users]
        return {'users': users}

    def parse_update_status(self, packet):
        pass

    def parse_clan_message(self, packet):
        pass

    def parse_looking_for_clan(self, packet):
        pass

    def parse_private_message(self, packet):
        """ A private message from anyone.
            Returns two variables.
                `player`    The name of the player who sent the whisper.
                `message`   The full message sent in the whisper.
            Packet ID: 0x1C
        """
        c = Struct("packet", CString("name"), CString("message"))
        r = c.parse(packet)
        return {"player" : r.name, "message" : r.message }

    def parse_private_message_failed(self, packet):
        pass

    def parse_whisper_buddies(self, packet):
        pass

    def parse_max_channels(self, packet):
        pass

    def parse_user_info_no_exist(self, packet):
        pass

    def parse_user_info_offline(self, packet):
        pass

    def parse_user_info_online(self, packet):
        pass

    def parse_user_info_ingame(self, packet):
        pass

    def parse_channel_update(self, packet):
        pass

    def parse_channel_update_topic(self, packet):
        pass

    def parse_channel_kick(self, packet):
        pass

    def parse_channel_ban(self, packet):
        pass

    def parse_channel_unban(self, packet):
        pass

    def parse_channel_banned(self, packet):
        pass

    def parse_channel_silenced(self, packet):
        pass

    def parse_channel_silence_lifted(self, packet):
        pass

    def parse_channel_silence_placed(self, packet):
        pass

    def parse_message_all(self, packet):
        pass

    def parse_channel_promote(self, packet):
        pass

    def parse_channel_demote(self, packet):
        pass

    def parse_channel_auth_enable(self, packet):
        pass

    def parse_channel_auth_disable(self, packet):
        pass

    def parse_channel_auth_add(self, packet):
        pass

    def parse_channel_auth_delete(self, packet):
        pass

    def parse_channel_auth_list(self, packet):
        pass

    def parse_channel_password_changed(self, packet):
        pass

    def parse_channel_auth_add_fail(self, packet):
        pass

    def parse_channel_auth_del_fail(self, packet):
        pass

    def parse_join_channel_password(self, packet):
        pass

    def parse_channel_emote(self, packet):
        pass
    
    def parse_total_online(self, packet):
        """ Gets the number of players online
            Packet ID: 0x68
        """
        c = Struct("players_online",
               ULInt32('count'),
               CString('regions')
            )
        r = c.parse(packet)
        return {'count': r.count, 'region_data': r.regions} 
    
    def parse_request_notification(self, packet):
        """ Gets a buddy invite from another player
        Packet ID : 0xB2
        """
        c = Struct("buddy_invite", 
                Byte("unknown_byte"), 
                ULInt16("pass_int"), 
                Byte('status'),
                Byte('flags'),
                CString('player')
            )
        r = c.parse(packet)
        print 'PASS INT : %s'%r.pass_int
        return {'player' : r.player, 'pass_int' : r.pass_int}

    def parse_notification(self, packet):
        pass

    def parse_game_invite(self, packet):
        """ Gets a game invite from another player
        Packet ID : 0x25
        """
        c = Struct("game_invite",
               CString('name'),
               CString('unknown'),
               CString('server_ip')
            )
        r = c.parse(packet)
        return {'player' : r.name, 'server_ip' : r.server_ip}

class GameSocket:
    """ Represents the socket connected to a game server.
        This object will be created once with the client, and only one will 
        be maintained, however the socket and listener will be re-created for
        each connection used. GC should pick up the old and unused ones.
    """
    def __init__(self, game_events):
        self.socket = None
        self.connected = False
        self.authenticated = False
        self.listener = None
        self.packet_parser = GamePacketParser()
        self.events = game_events
        self.senders = {}
        
        # We have to count the number of 0x03 packets we send
        self.zero_x_zero_three_count = 0x00

        # Transparently connect the ping event to the pong sender.
        self.events[HON_GSC_PING].connect(self.send_pong, priority=1)

        # Some internal handling of the authentication process is also needed
        self.events[HON_GSC_AUTH_ACCEPTED].connect(self.on_auth_accepted, priority=1)
        self.events[HON_GSC_SERVER_STATE].connect(self.on_server_state, priority=1)
        self.events[HON_GSC_SERVER_INFO].connect(self.on_server_info, priority=1)
        
        
    def get_0x03_count(self):
        self.zero_x_zero_three_count += 1
        return self.zero_x_zero_three_count
        
    @property
    def is_authenticated(self):
        """ The GameSocket becomes authenticated with the Game Server once
            the `auth_accepted` packet has been received. The GameSocket will
            then be authenticated until the connection is lost.
        """
        return self.authenticated

    @property
    def is_connected(self): 
        """ Upon forgetting to connect the ping handler, it is possible to
            see the effects of a ping timeout. This means the socket can be terminated
            early by the server, but at this time it will just leave the program
            hanging. This is_connected method needs to be expanded to check for ping
            timeouts possibly, or simply if the socket is still connected or the 
            socket listener is still running.
        """
        # The socket has been broken early.
        if self.listener.stopped is True and self.connected is True:
            self.connected = False
            self.authenticated = False
        return self.connected

    def connect(self, address, port):
        """ Creates a connection to the game server and starts listening for packets.
        """
        
        self.address = address
        self.port = port
        
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            #self.socket.bind(("", 0))
            self.socket.connect((address, port))
        except socket.timeout:
            raise HoNCoreError(11)
        except socket.error, e:
            if e.errno == 110:
                raise HoNCoreError(11) # Socket timed out
            raise HoNCoreError(10) # Socket error
        
        # The socket is now actually connected.
        self.connected = True

        # Set up a listener as the socket can send data now.
        self.listener = SocketListener(self)
        self.listener.start()

    def disconnect(self):
        """ Disconnecting should not fail, it's a pretty forced procedure.
            Set the internal state of the socket to be disabled, and set
            authenticated to False.
            Clear the socket object and listener so they can be dereferenced.
        """

        self.connected = False
        self.authenticated = False

        try:
            self.socket.shutdown(socket.SHUT_RDWR)
            self.socket.close()
        except socket.error, e:
            raise HoNCoreError(10) # Socket Error
        finally:
            self.socket = None

        self.listener.stopped = True
        self.listener.join()
        self.listener = None
        
        for sender_packet, sender in self.senders.items():
            sender.stopped = True
            sender.join()
        
        self.senders = None
    
    def send(self, data):
        """ Wrapper send method. 
            TODO: Capture failed sends.
            TODO: Possibly check for the authentication first, and authenticate if required.
        """
        #print "Sending on socket %s from thread %s" % (self.socket, threading.currentThread().getName())
        try:
            self.socket.send(data)
            print ">>GAME %s | %s"%(len(data), struct.unpack('%ss'%len(data), data))
        except socket.error, e:
            #print "Socket error %s while sending." % e
            raise
        return True

    def recv(self):
        
        packet = self.socket.recv(4096)
        
#        # Receive until the entire packet has been received.
#        received_len = to_get = 0
#        while received_len < packet_len:
#            to_get = packet_len - received_len
#            packet += self.socket.recv(to_get)
#            received_len = len(packet) - 2
        
        return packet
    
    def parse_packet(self, packet):
        """ Core function to tie together all of the packet parsing. """
        packet_id = self.packet_parser.parse_id(packet)

        # Trigger a general event on all packets. Passes the raw packet.
        try:
            self.events[HON_GSC_PACKET_RECV].trigger(**{'packet_id': packet_id, 'packet': packet})
        except KeyError:
            print 'KEY ERROR ON PARSE PACKET'
            pass
        
        # Trim the packet id from the packet 0000030300000067 -> 0300000067
        packet = packet[3:]
        
        try:
            packet_data = self.packet_parser.parse_data(packet_id, packet)
        except HoNCoreError, e:
            if e.code == 12: # Unknown packet received.    
                return False

        if packet_data is None:
            return
        
        if packet_id in self.events:
            event = self.events[packet_id]
            event.trigger(**packet_data)
            
    def add_sender(self, period, packet):
        """ Add a new SocketSender to our GameSocket """
        sender = SocketSender(self, period, packet)
        self.senders.update({packet : sender})
        self.senders[packet].start()

    def on_auth_accepted(self, *p):
        """ Set the authenticated state to True"""
        self.authenticated = True
        
    def on_server_info(self, packet_body = None, trash = None):
        """ React to the game server info
        """
        
        if packet_body:
            # Send the heartbeat response packet
            
            heartbeat_struct = Struct("server_heartbeat",
                    ULInt16("hon_connection_id"), 
                    Byte('heartbeat_byte'), 
                    ULInt16("heartbeat_int"),
                    ULInt32("packet_body"), 
                    Byte("end_byte")
            )
            
            heartbeat_packet = heartbeat_struct.build(Container(
                                                hon_connection_id = HON_CONNECTION_ID, 
                                                heartbeat_byte = 1,
                                                heartbeat_int = 8391, 
                                                packet_body = packet_body,
                                                end_byte = 0,
                                                ))
                                                
            self.send(heartbeat_packet)
            
        
    def on_server_state(self, packet_body, packet_second_id, packet_third_id):
        """ Send the server_state response to the game server
            packetHeader[headerIndex][0], 
            packetHeader[headerIndex][1], 
            0x05, 
            packet[3], 
            packet[4], 
            packet[5], 
            packet[6]
        """
        
        c = Struct("server_state_response",
                ULInt16("packet_header"), 
                Byte('server_state_response_byte'), 
                ULInt32("packet_body")
        )
        
        if packet_second_id == 0x01 and packet_third_id == 0x00:
            packet_header = 0
            loading_state = 194
        else:
            packet_header = HON_CONNECTION_ID
            loading_state = 196
        
        # Send the "i got it!" packet
        packet = c.build(Container(
                                    packet_header=packet_header, 
                                    server_state_response_byte = 5, 
                                    packet_body=packet_body))
    
        try:
            self.send(packet)
        except socket.error, e:
            if e.errno == 32:
                raise GameServerError(206)
                
        # If not joining a game, send the corresponding loading_state packet
        
        if self.creating:
            loading_state_struct = Struct("loading_state_c2", 
                              ULInt16("hon_connection_id"), 
                              Byte("magic_byte"), 
                              ULInt32("packet_body"), 
                              Byte('loading_state'))
                              
            loading_state_packet = loading_state_struct.build(Container(
                                                hon_connection_id = HON_CONNECTION_ID, 
                                                magic_byte = 3,
                                                packet_body = packet_body,
                                                loading_state = loading_state
                                                ))
                                                
            self.send(loading_state_packet)

    def send_pong(self):
        print 'GAME PONG'
        self.send(struct.pack('H', HON_CS_PONG))

    def create_game(self, game_name, player_name, cookie, ip, acc_key, account_id, acc_key_hash, auth_hash):
        """ Sends the chat server authentication request.
            Takes 7 parameters.
                `player_name`   A string containing the player name 
                `cookie`        A 32 character string containing a cookie.
                `ip`            A string containing the player's IP address.
                `acc_key`       A 32 character string containing the acc_key, provided by masterserver
                `account_id`    An integer containing the player's account ID.
                `acc_key_hash`  A 40 character string containing a hash of the acc_key, provided by masterserver
                `auth_hash`     A 40 character string containing an authentication hash.
        """
    
        self.creating = True
        self.joining = False
        
        c = Struct("game_server_auth",
                ULInt16("header_int"), 
                ULInt16("id"),
                String("hon_name", len("Heroes of Newerth")+1, encoding="utf8", padchar = "\x00"),
                String("server_version", len(HON_SERVER_VERSION)+1, encoding="utf8", padchar = "\x00"),
                ULInt32("host_id"), 
                ULInt16("connection_id"), 
                Byte("break_byte"), 
                String("player_name", len(player_name)+1, encoding="utf8", padchar = "\x00"),
                String("cookie", len(cookie)+1, encoding="utf8", padchar = "\x00"),
                String("ip", len(ip)+1, encoding="utf8", padchar = "\x00"),
                String("acc_key", len(acc_key)+1, encoding="utf8", padchar = "\x00"), 
                ULInt16("magic_int"), 
                ULInt32("account_id"), 
                String("auth_hash", len(auth_hash)+1, encoding="utf8", padchar="\x00"), 
                String("acc_key_hash", len(acc_key_hash)+1, encoding="utf8", padchar = "\x00"),
                ULInt32("magic_int2"), 
                ULInt32("magic_int3"), 
                ULInt32("magic_int4"), 
                ULInt16("magic_int5")
        )

        packet = c.build(Container(header_int=0, 
                                   id=HON_CGS_AUTH_INFO, 
                                    hon_name=unicode("Heroes of Newerth"), 
                                    server_version=unicode(HON_SERVER_VERSION), 
                                    host_id=HON_HOST_ID, 
                                    connection_id=HON_CONNECTION_ID,
                                    break_byte = 0, 
                                    player_name=unicode(player_name),
                                    cookie=unicode(cookie), 
                                    ip=unicode(ip), 
                                    acc_key=unicode(acc_key), 
                                    magic_int=0x100, 
                                    account_id=account_id, 
                                    auth_hash=unicode(auth_hash), 
                                    acc_key_hash=unicode(acc_key_hash), 
                                    magic_int2=0x5140000, 
                                    magic_int3=0x4e200000, 
                                    magic_int4=0x140000, 
                                    magic_int5=0x0))
        
        try:
            self.send(packet)
            self.authenticated = True
        except socket.error, e:
            if e.errno == 32:
                raise GameServerError(206)
                
#        # Send the 12 first magic packets
#        
#        magic_c = Struct("magic_packet",
#                ULInt16("hon_connection_id"), 
#                ULInt16("magic_int"))
#        
#        magic_packet = magic_c.build(Container(
#                                         hon_connection_id = 0, 
#                                         magic_int = 0xc901
#                                         ))
#                                         
#        try:
#            for i in range(12):
#                self.send(magic_packet)
#        except socket.error, e:
#            raise GameServerError()
#        
#        
#        time.sleep(1)
#        
#       # Setup a SocketSender for packet [HON_CONNECTION_ID]01c9
#        
#        periodic_c = Struct("periodic_packet", 
#                          ULInt16("hon_connection_id"), 
#                          Byte("magic_byte"), 
#                          Byte("magic_byte2"))
#                          
#        periodic_packet = periodic_c.build(Container(
#                                            hon_connection_id = HON_CONNECTION_ID, 
#                                            magic_byte = 1, 
#                                            magic_byte2 = 201, 
#                                            ))
#                                            
#        self.add_sender(period = 0.3, packet = periodic_packet)
#        
#        
#        time.sleep(5)
    
        # Send game options
        
        game_options = "map:caldavar mode:normal region: teamsize:5 spectators:0 referees:0 minpsr:0 maxpsr:0 private:false noleaver:false nostats:false alternatepicks:false norepick:false noswap:false noagility:false nointelligence:false nostrength:false norespawntimer:false dropitems:false nopowerups:false casual:false allowduplicate:false shuffleteams:false tournamentrules:false hardcore:false devheroes:false autobalance:false verifiedonly:false "
        
        c = Struct("send_game_options",
                ULInt16("connection_id"), 
                ULInt16("magic_int"), 
                Byte("magic_byte"), 
                ULInt32("magic_int2"), 
                String("game_name", len(game_name)+1, encoding="utf8", padchar = "\x00"),
                String("game_options", len(game_options)+1, encoding="utf8", padchar = "\x00"),
        )

        packet = c.build(Container(connection_id = HON_CONNECTION_ID, 
                                   magic_int=7171, 
                                   magic_byte=0, 
                                   magic_int2=449314816, 
                                   game_name = game_name, 
                                   game_options = game_options))
                                   
        try:
            self.send(packet)
            self.authenticated = True
        except socket.error, e:
            if e.errno == 32:
                raise GameServerError(206)
        
                
    def join_game(self, player_name, cookie, ip, account_id, auth_hash):
        """ Join a game already created
            Takes 7 parameters.
                `player_name`   A string containing the player name 
                `cookie`        A 32 character string containing a cookie.
                `ip`            A string containing the player's IP address.
                `acc_key`       A 32 character string containing the acc_key, provided by masterserver
                `account_id`    An integer containing the player's account ID.
                `acc_key_hash`  A 40 character string containing a hash of the acc_key, provided by masterserver
                `auth_hash`     A 40 character string containing an authentication hash.
        """
        
        self.joining = True
        self.creating = False

        # Send authentication packet

        c = Struct("join_game",
                ULInt16("header_int"), 
                ULInt16("id"),
                String("hon_name", len("Heroes of Newerth")+1, encoding="utf8", padchar = "\x00"),
                String("server_version", len(HON_SERVER_VERSION)+1, encoding="utf8", padchar = "\x00"),
                ULInt32("host_id"), 
                ULInt16("connection_id"), 
                Byte("break_byte"), 
                String("player_name", len(player_name)+1, encoding="utf8", padchar = "\x00"),
                String("cookie", len(cookie)+1, encoding="utf8", padchar = "\x00"),
                String("ip", len(ip)+1, encoding="utf8", padchar = "\x00"),
                ULInt16("magic_int"), 
                Byte("break_byte2"), 
                ULInt32("account_id"), 
                String("auth_hash", len(auth_hash)+1, encoding="utf8", padchar="\x00"), 
                Byte("break_byte3"), 
                ULInt32("magic_int2"), 
                ULInt32("magic_int3"), 
                ULInt32("magic_int4"), 
                ULInt16("magic_int5")
        )

        packet = c.build(Container(header_int=0, 
                                   id=HON_CGS_AUTH_INFO, 
                                    hon_name=unicode("Heroes of Newerth"), 
                                    server_version=unicode(HON_SERVER_VERSION), 
                                    host_id=HON_HOST_ID, 
                                    connection_id=HON_CONNECTION_ID,
                                    break_byte = 0, 
                                    player_name=unicode(player_name),
                                    cookie=unicode(cookie), 
                                    ip=unicode(ip), 
                                    magic_int=0, 
                                    break_byte2=0, 
                                    account_id=account_id, 
                                    auth_hash=unicode(auth_hash), 
                                    break_byte3=0, 
                                    magic_int2=0x5140000, 
                                    magic_int3=0x4e200000, 
                                    magic_int4=0x140000, 
                                    magic_int5=0x0))
        
        try:
            self.send(packet)
            self.authenticated = True
        except socket.error, e:
            if e.errno == 32:
                raise GameServerError(206)
                
#        # Setup a SocketSender for packet [HON_CONNECTION_ID]01c9
#        
#        periodic_c = Struct("periodic_packet", 
#                          ULInt16("hon_connection_id"), 
#                          Byte("magic_byte"), 
#                          Byte("magic_byte2"))
#                          
#        periodic_packet = periodic_c.build(Container(
#                                            hon_connection_id = HON_CONNECTION_ID, 
#                                            magic_byte = 1, 
#                                            magic_byte2 = 201, 
#                                            ))
#                                            
#        self.add_sender(period = 0.3, packet = periodic_packet)
                
        time.sleep(5)
        
        # Send "i begin loading" packet
        
        c = Struct("loaded_0",
                ULInt16("connection_id"), 
                Byte("id_byte"), 
                Byte("second_id_byte"), 
                ULInt32("progression_percent")
        )

        packet = c.build(Container(connection_id=HON_CONNECTION_ID,
                                   id_byte = 1, 
                                   second_id_byte = 206, 
                                   progression_percent = 0))
        
        try:
            self.send(packet)
            self.authenticated = True
        except socket.error, e:
            if e.errno == 32:
                raise GameServerError(206)
                
        # The server needs an authentication with the cookie
        
        c = Struct("load_auth",
                ULInt16("connection_id"), 
                Byte("id_byte"), 
                ULInt32("zeroxzerothree_count"), 
                ULInt16("magic_int"), 
                Byte("magic_byte"), 
                String("cookie", len(cookie)+1, encoding="utf8", padchar = "\x00"),
        )

        packet = c.build(Container(connection_id=HON_CONNECTION_ID,
                                   id_byte = 3, 
                                   zeroxzerothree_count = self.get_0x03_count(), 
                                   magic_int = 50370, 
                                   magic_byte = 208,
                                   cookie=unicode(cookie), 
                                   ))
        
        try:
            self.send(packet)
            self.authenticated = True
        except socket.error, e:
            if e.errno == 32:
                raise GameServerError(206)
    
#        # Send "i loaded 100% packet"
#        
#        c = Struct("loaded_100",
#                ULInt16("connection_id"), 
#                Byte("id_byte"), 
#                Byte("second_id_byte"), 
#                ULInt32("progression_percent")
#        )
#
#        packet = c.build(Container(connection_id=HON_CONNECTION_ID,
#                                   id_byte = 1, 
#                                   second_id_byte = 206, 
#                                   progression_percent = 1065353216))
#        
#        try:
#            self.send(packet)
#            self.authenticated = True
#        except socket.error, e:
#            if e.errno == 32:
#                raise GameServerError(206)
        
        
        # Send "i'm finished loading" packet
                
        c = Struct("join_game",
                ULInt16("connection_id"), 
                Byte("id_byte"), 
                ULInt32("zeroxzerothree_count"), 
                Byte('finished_loading_byte')
        )

        packet = c.build(Container(connection_id=HON_CONNECTION_ID,
                                   id_byte = 3, 
                                   zeroxzerothree_count = self.get_0x03_count(), 
                                   finished_loading_byte = 203))
        
        try:
            self.send(packet)
            self.authenticated = True
        except socket.error, e:
            if e.errno == 32:
                raise GameServerError(206)
                
        # Send the magic ending packet
        
        c = Struct("magic_ending",
                ULInt16("connection_id"), 
                Byte("id_byte"), 
                ULInt32("zeroxzerothree_count"), 
                ULInt32("magic_int"), 
                ULInt32("magic_int2"), 
                ULInt32("magic_int3"), 
                Byte("magic_byte"), 
        )

        packet = c.build(Container(connection_id=HON_CONNECTION_ID,
                                   id_byte = 3, 
                                   zeroxzerothree_count = self.get_0x03_count(), 
                                   magic_int = 29345989,
                                   magic_int2 = 16777216,
                                   magic_int3 = 1, 
                                   magic_byte = 0))
        
        try:
            self.send(packet)
            self.authenticated = True
        except socket.error, e:
            if e.errno == 32:
                raise GameServerError(206)
        
        self.join_spec()
        
    def swap_players(self, first_slot, second_slot):
        """ Swap 2 player slots
            Takes 2 parameters.
                `first_slot`       A string corresponding to the first slot color.
                `second_slot`      A string corresponding to the second slot color.
        """
        
        c = Struct("swap_players",
                ULInt16("connection_id"), 
                Byte("id_byte"), 
                ULInt32("zeroxzerothree_count"), 
                ULInt16("swap_slot_int"), 
                Byte('team1_byte'), 
                Byte('null_byte'), 
                Byte('null_byte2'), 
                Byte('null_byte3'),
                Byte('slot1_byte'), 
                Byte('null_byte4'),
                Byte('null_byte5'),
                Byte('null_byte6'), 
                Byte('team2_byte'), 
                Byte('null_byte7'), 
                Byte('null_byte8'), 
                Byte('null_byte9'),
                Byte('slot2_byte'), 
                Byte('null_byte10'),
                Byte('null_byte11'),
                Byte('null_byte12')
        )

        packet = c.build(Container(connection_id=HON_CONNECTION_ID,
                                    id_byte = 3, 
                                    zeroxzerothree_count = self.get_0x03_count(), 
                                    swap_slot_int = 20424, 
                                    team1_byte = TEAM_SLOTS[first_slot][0], 
                                    null_byte = 0, 
                                    null_byte2 = 0, 
                                    null_byte3 = 0, 
                                    slot1_byte = TEAM_SLOTS[first_slot][1], 
                                    null_byte4 = 0, 
                                    null_byte5 = 0, 
                                    null_byte6 = 0, 
                                    team2_byte = TEAM_SLOTS[second_slot][0], 
                                    null_byte7 = 0, 
                                    null_byte8 = 0, 
                                    null_byte9 = 0, 
                                    slot2_byte = TEAM_SLOTS[second_slot][1], 
                                    null_byte10 = 0, 
                                    null_byte11 = 0, 
                                    null_byte12 = 0
                                   ))
        
        self.send(packet)
        
    def join_spec(self):
        #6a61034f000000c80100000000ffffffff SPEC
        """ Join a spectator slot
        """
        
        c = Struct("join_spec",
                ULInt16("connection_id"), 
                Byte("id_byte"), 
                ULInt32("zeroxzerothree_count"), 
                ULInt16("join_team_int"), 
                ULInt32("null_int"), 
                ULInt32("spec_tail")
        )

        packet = c.build(Container(connection_id=HON_CONNECTION_ID,
                                    id_byte = 3, 
                                    zeroxzerothree_count = self.get_0x03_count(), 
                                    join_team_int = 456, 
                                    null_int = 0, 
                                    spec_tail = 4294967295
                                   ))
        
        self.send(packet)
        
    
    def join_team(self, team_slot):
        #0019
        #6a61035b000000c8010200000000000000 PINK
        #send(new int[]{packetHeader[headerIndex][0], packetHeader[headerIndex][1], 0x03, count[0], count[1], count[2], count[3], 0xc8, 0x01, team, 0x00, 0x00, 0x00, slot, 0x00, 0x00, 0x00});
        """ Join a team in the game
            Takes 1 parameter.
                `team_slot`       A string corresponding to a team slot.
        """
        
        c = Struct("join_team",
                ULInt16("connection_id"), 
                Byte("id_byte"), 
                ULInt32("zeroxzerothree_count"), 
                ULInt16("join_team_int"), 
                Byte('team_byte'), 
                Byte('null_byte'), 
                Byte('null_byte2'), 
                Byte('null_byte3'),
                Byte('slot_byte'), 
                Byte('null_byte4'),
                Byte('null_byte5'),
                Byte('null_byte6')
        )

        packet = c.build(Container(connection_id=HON_CONNECTION_ID,
                                    id_byte = 3, 
                                    zeroxzerothree_count = self.get_0x03_count(), 
                                    join_team_int = 456, 
                                    team_byte = TEAM_SLOTS[team_slot][0], 
                                    null_byte = 0, 
                                    null_byte2 = 0, 
                                    null_byte3 = 0, 
                                    slot_byte = TEAM_SLOTS[team_slot][1], 
                                    null_byte4 = 0, 
                                    null_byte5 = 0, 
                                    null_byte6 = 0
                                   ))
        
        self.send(packet)

        
    def send_game_message(self, message):
        """ Sends the message to the game lobby
            Takes 1 parameter.
                `message`       A string containing the message.
        """
        c = Struct("message",
                ULInt16("id"),
                String("message", len(message)+1, encoding="utf8", padchar="\x00"),
        )
        packet = c.build(Container(id=HON_CS_CHANNEL_MSG, message=unicode(message)))
        self.send(packet)

class GamePacketParser:
    """ A class to handle raw packet parsing. """
    def __init__(self):
        self.__packet_parsers = {}
        self.__setup_parsers()

    def __setup_parsers(self):
        """ Add every known packet parser to the list of availble parsers. """
        self.__add_parser(HON_GSC_PING, self.parse_ping)
        self.__add_parser(HON_GSC_CHANNEL_MSG, self.parse_game_message)
        self.__add_parser(HON_GSC_TIMEOUT, self.parse_timeout)
        self.__add_parser(HON_GSC_SERVER_STATE, self.parse_server_state)
        self.__add_parser(HON_GSC_SERVER_INFO, self.parse_server_info)
    
    def __add_parser(self, packet_id, function):
        """ Registers a parser function for the specified packet. 
            Ensures that only one parser exists for each packet.
        """
        if packet_id in self.__packet_parsers:
            return False
        self.__packet_parsers[packet_id] = function

    def parse_id(self, packet):
        """ Returns the packet's ID.
            The ID is a 1 byte, which is located at bytes 3 
            within the packet.
        """
        return struct.unpack('B', packet[2])[0]

    def parse_data(self, packet_id, packet):
        """ Pushes the packet through to a matching registered packet parser, which extracts any useful data 
            into a dict of kwargs which can then be handed to any matching registered event handler.

            Passes the packet to a packet parser so it can be parsed for data. The returned data
            is then passed to each event handler that requests it as a list of named keywords which
            are taken as arguments.
        """
        if packet_id in self.__packet_parsers:
            parser = self.__packet_parsers[packet_id]
            data = parser(packet)
            return data
        else:
            raise HoNCoreError(12) # Unknown packet received.

    def parse_ping(self, packet):
        """ Pings sent every minute. Respond with pong. 
            Packet ID: 
        """
        return {}
        
    def parse_timeout(self, packet):
        """ Game server timeout. When received, disconnect client from game server 
            Packet ID: 0x5101
        """
        return {}
        
    def parse_server_state(self, packet):
        """ Game server state. When received, tell the server that there is no need to send it again 
            Packet ID: 0x03
        """
        
        c = Struct('game_server_state',
                    ULInt32('packet_body'), 
                    String('message', len(packet)-4)
                  )
        r = c.parse(packet)
        
        return {
            'packet_body' : r.packet_body, 
            'packet_second_id' : struct.unpack('B', packet[0])[0], 
            'packet_third_id' : struct.unpack('B', packet[1])[0]
        }
        
    def parse_server_info(self, packet):
        """ Game server info. When received, tell the server that we are alive 
            Packet ID: 0x01
        """
        
        # Game server HeartBeat case
#        print 'HEARTBEAT RECEIVED, INFO ID : %s'%struct.unpack('H', packet[0:2])[0]
        #0000015a0e000000c765650096504305400280506e00
        
        if len(packet)>9:
            c = Struct('game_server_heartbeat',
                        ULInt32('trash_int'), 
                        Byte('trash_byte'), 
                        ULInt32('packet_body'), 
                        String('trash', len(packet)-9)
                      )
            r = c.parse(packet)
            
            return {
                'packet_body' : r.packet_body, 
            }
        else:
            c = Struct('game_server_info', 
                       String('trash', len(packet)))
            r = c.parse(packet)
            
            return {
                'trash' : r.trash, 
            }
    

    def parse_game_message(self, packet):
        """ Triggered when a message is sent to the game lobby.
            Returns the following:
                `message`       The message sent.
            Packet ID: 
        """
        c = Struct('game_message',
                   ULInt32('account_id'),
                   ULInt32('channel_id'),
                   CString('message')
                  )
        r = c.parse(packet)
        return {
            'message'   : r.message
        }
        
