""" 
HoNCore. Python library providing connectivity and functionality
with HoN's chat server.
"""

import tempfile, subprocess, re, time

def ping_server(server_ip):
    print 'Pinging server with IP : %s'%server_ip
    tmp_file = tempfile.TemporaryFile()    
    subprocess.call(['ping', '-c', '1', server_ip], stdout = tmp_file)
    tmp_file.seek(0)
    ping_lines = tmp_file.read()
    tmp_file.close()
    try:
        ping_value = float(re.findall('time=(.*) ms', ping_lines)[0])
        print "Ping : %s"%ping_value
        return ping_value
    except Exception, e :
        print 'Error while pinging server %s : %s %s'%(server_ip, re.findall('time=(.*) ms', ping_lines), e)
        raise Exception("Cannot reach Game Server")    
    
def HexToByte( hexStr ):
    """
    Convert a string hex byte values into a byte string. The Hex Byte values may
    or may not be space separated.
    """
    # The list comprehension implementation is fractionally slower in this case    
    #
    #    hexStr = ''.join( hexStr.split(" ") )
    #    return ''.join( ["%c" % chr( int ( hexStr[i:i+2],16 ) ) \
    #                                   for i in range(0, len( hexStr ), 2) ] )
 
    bytes = []

    hexStr = ''.join( hexStr.split(" ") )

    for i in range(0, len(hexStr), 2):
        bytes.append( chr( int (hexStr[i:i+2], 16 ) ) )

    return ''.join( bytes )
    
def ByteToHex( byteStr ):
    """
    Convert a byte string to it's hex string representation e.g. for output.
    """
    
    # Uses list comprehension which is a fractionally faster implementation than
    # the alternative, more readable, implementation below
    #   
    #    hex = []
    #    for aChar in byteStr:
    #        hex.append( "%02X " % ord( aChar ) )
    #
    #    return ''.join( hex ).strip()        

    return ''.join( [ "%02X" % ord( x ) for x in byteStr ] ).strip()
