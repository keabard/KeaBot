""" 
HoNCore. Python library providing connectivity and functionality
with HoN's chat server.
"""

import tempfile, subprocess, re

def ping_server(server_ip):
    print 'Pinging server with IP : %s'%server_ip
    tmp_file = tempfile.TemporaryFile()    
    subprocess.call(['ping', '-c', '1', server_ip], stdout = tmp_file)
    tmp_file.seek(0)
    ping_lines = tmp_file.read()
    tmp_file.close()
    try:
        ping_value = float(re.findall('time=(.*) ms', ping_lines)[0])
    except Exception, e :
        print 'Error while pinging server %s : %s %s'%(server_ip, re.findall('time=(.*) ms', ping_lines), e)
        return -1       
    
    return ping_value