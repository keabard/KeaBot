import time, sys, getpass, signal, struct
from lib.honcore.client import HoNClient
from lib.honcore.exceptions import *
from lib.honcore.constants import *
from hashlib import md5


class BasicHoNClient(HoNClient):
    def __init__(self):
        super(BasicHoNClient, self).__init__()
        self.logged_in = False
        self.setup_events()

    def setup_events(self):
        self.connect_event(HON_SC_PACKET_RECV, self.__on_packet)
        self.connect_event(HON_GSC_PACKET_RECV, self.__on_game_packet)
        self.connect_event(HON_SC_AUTH_ACCEPTED, self.on_authenticated)
        self.connect_event(HON_SC_WHISPER, self.on_whisper)
        self.connect_event(HON_SC_JOINED_CHANNEL, self.on_joined_channel)
        self.connect_event(HON_SC_GAME_INVITE, self.on_game_invite)
        self.connect_event(HON_SC_REQUEST_NOTIFICATION, self.on_buddy_invite)

    def __on_packet(self, packet_id, packet):
        print "<<CHAT 0x%x | %i bytes" % (packet_id, len(packet))
        """ Pipe the raw packet to a file for debugging. """
        f = open("raw-packets/0x%x" % packet_id, "a+")
        #print "%s (%s)"%(struct.unpack('<H%ss'%(len(packet[2:])-2), packet[2:]), struct.unpack('>H', packet[:2]))
        print >>f, "%s (%s)"%(struct.unpack('<H%ss'%(len(packet[2:])-2), packet[2:]), struct.unpack('>H', packet[:2]))
        #f.flush()
        f.close()
        
    def __on_game_packet(self, packet_id, packet):
        print "<<GAME 0x%x | %i bytes | %s" % (packet_id, len(packet), struct.unpack('%ss'%len(packet), packet))
        """ Pipe the raw packet to a file for debugging. """
        if len(packet) > 2:
            f = open("raw-packets/game-0x%x" % packet_id, "a+")
            #print "%s (%s)"%(struct.unpack('<H%ss'%(len(packet[2:])-2), packet[2:]), struct.unpack('>H', packet[:2]))
            print >>f, "(%s) %s"%( len(packet), struct.unpack('<%ss'%len(packet), packet))
            #f.flush()
            f.close()

    def on_authenticated(self):
        print "Connected"
        time.sleep(2)
        for buddy in self.get_buddies():
            if buddy.status != HON_STATUS_OFFLINE:
                print "%s is online" % buddy
        time.sleep(2)
        self.create_game("Test")
        time.sleep(1)

    def on_whisper(self, player, message):
        print "Whisper from %s : %s"%(player, message)
        if 'invite' in message:
            self.send_game_invite(player)
        elif 'create game ' in message:
            self.create_game(message.split('create game ')[1])
        elif 'buddy' in message:
            self.send_buddy_add_notify(player)
        
    def on_joined_channel(self, channel, channel_id, topic, operators, users):
        print "Joined channel %s" % channel
        op_count = 0
        normal_count = 0
        nicknames = []
        for user in users:
            nickstr = ''
            if user.account_id in operators:
                nickstr += '@'
                op_count += 1
            else:
                normal_count += 1
            nickname = self.id_to_nick(user.account_id)
            nickstr += ' %s' % nickname
            nicknames.append(nickstr)
        
        max_len = len(max(nicknames, key=len))
        users_str = ''
        c = 0
        for nick in nicknames:
            users_str += '[%*s] ' % (max_len, nick)
            c += 1
            if c == 5:
                users_str += '\n'
                c = 0

        print users_str
        print "%s Users [%s ops, %s normal]" % (len(nicknames), op_count, normal_count)

    def on_game_invite(self, player, server_ip):
        print "Game invite from %s : %s"%(player, server_ip)
        self.send_whisper(player, "Merci pour l'invite connard")
        
    def on_buddy_invite(self, player, pass_int):
        print 'BUDDY INVITE RECEIVED : %s %s'%(player, pass_int)
        self.send_buddy_accept(player)

    @property
    def is_logged_in(self):
        return self.logged_in

    def configure(self, protocol=None, invis=False):
        self._configure(protocol=protocol, invis=invis)

    def login(self, username, password):
        print "Logging in..."
        try:
            self._login(username, password)
        except MasterServerError, e:
            print e
            return False
        self.logged_in = True
        
        print "Connecting..."
        try:
            self._chat_connect()
        except ChatServerError, e:
            print e
            return False

    def connect(self):
        try:
            self._chat_connect()
        except ChatServerError, e:
            print e
            return False
        return True
    
    def logout(self):
        
        if self.is_connected_to_game:
            try:
                print "Disconnecting from game..."
                self._game_disconnect()
            except GameServerError, e:
                print e
            
        if not self.is_connected:
            self.logged_in = False
            return
        try:
            print "Disconnecting from chat server..."
            self._chat_disconnect()
        except ChatServerError, e:
            print e
        
        print "Logging out..."
        try:
            self._logout()
        except MasterServerError, e:
            print e
        self.logged_in = False


def main():
    client = BasicHoNClient()
    client.configure(protocol=22, invis=False)

    def sigint_handler(signal, frame):
        print "SIGINT, quitting..."
        if client.is_logged_in and client.is_connected:
            client.logout()
        sys.exit()

    signal.signal(signal.SIGINT, sigint_handler)

    reconnect_attempts = 0
    while True:
        username = raw_input("Username: ")
        password = getpass.getpass()
        password = md5(password).hexdigest()
        client.login(username, password)

        while client.is_logged_in:
            if client.is_connected:
                reconnect_attempts = 0
                time.sleep(1)
            else:
                reconnect_attempts += 1
                print "Disconnected from the chat server"
                print "Reconnecting in 30 seconds (Attempts %d of 5)" % reconnect_attempts
                time.sleep(30)
                try:
                    client.connect()
                except ChatServerError, e:
                    print e

if __name__ == '__main__':
    main()
