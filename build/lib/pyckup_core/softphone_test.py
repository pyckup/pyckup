import os
import time
from softphone import Softphone

# os.environ['SIP_CREDENTIALS_PATH'] = "/home/ubuntu/call-e/sip_credentials_alt.json"
sf = Softphone('/home/ubuntu/call-e/sip_credentials_alt.json')
sf.call("+4933123189599")
# sf.call("sip:01745332797@sip.easybell.de")
print('calling...')
sf.wait_for_stop_calling()

if sf.has_picked_up_call():
    print('picked up')
else:
    print('declined')
    exit()
    


while(sf.has_picked_up_call()):
    # msg = sf.listen()
    # print(msg)
    # msg = input()
    # sf.say(msg)
    # sf.forward_call("+4933123186053")
    sf.say("Hallo hallo")
    input()
    success = sf.forward_call("+4933176993008", timeout=10)
    if success:
        print('forwarded')
    else:
        print('failed to forward')
