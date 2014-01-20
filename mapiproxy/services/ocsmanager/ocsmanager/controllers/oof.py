import logging
import codecs
import traceback
import urllib
import base64
import struct
import ldb
import os, os.path, shutil
import string
import json

from pylons import request, response, session, tmpl_context as c, url
from pylons.controllers.util import abort, redirect
from pylons.decorators.rest import restrict
from pylons import config
from xml.etree.ElementTree import Element, ElementTree, tostring, register_namespace
from cStringIO import StringIO
from time import time, strftime, localtime

from ocsmanager.lib.base import BaseController, render

log = logging.getLogger(__name__)

namespaces = {
    'q': 'http://schemas.xmlsoap.org/soap/envelope/',
    'm': 'http://schemas.microsoft.com/exchange/services/2006/messages',
    't': 'http://schemas.microsoft.com/exchange/services/2006/types',
}

class ServerException(Exception):
    pass

class InvalidRequestException(Exception):
    pass

class AccessDeniedException(Exception):
    pass

class DbException(Exception):
    pass

class OofHandler(object):
    """
    This class parses the XML request, interprets it, find the requested
    answers and spills back an appropriate XML response.
    """

    def __init__(self, env):
        self.http_server_name = None
        self.target = None
        self.username = None
        self.workstation = None

        # Set the auth info
        self.decode_ntlm_auth(env)
        # Set the http_server_name
        server_env_names = iter(["HTTP_X_FORWARDED_SERVER",
                                 "HTTP_X_FORWARDED_HOST",
                                 "HTTP_HOST"])
        try:
            while self.http_server_name == None:
                env_name = server_env_names.next()
                if env_name in env:
                    self.http_server_name = (env[env_name].split(":"))[0]
        except StopIteration:
            pass

    def decode_ntlm_auth(self, env):
        """
        Decode the HTTP_AUTHORIZATION header and extract the target domain,
        the username and the workstation
        """
        header = env['HTTP_AUTHORIZATION']
        if header.startswith('NTLM '):
            blob_b64 = header[5:];
            blob = base64.b64decode(blob_b64)
            (signature, msgtype) = struct.unpack('@ 8s I', blob[0:12])
            if (msgtype == 3):
                (tgt_len, tgt_alloc, tgt_offset) = struct.unpack('@h h I', blob[28:36])
                if tgt_len > 0:
                    self.target = blob[tgt_offset:tgt_offset + tgt_len]
                    self.target = self.target.decode('UTF-16')

                (user_len, user_alloc, user_offset) = struct.unpack('@h h I', blob[36:44])
                if user_len > 0:
                    self.username = blob[user_offset:user_offset + user_len]
                    self.username = self.username.decode('UTF-16')
                    self.username = self.username.split('@')[0]

                (wks_len, wks_alloc, wks_offset) = struct.unpack('@h h I', blob[44:52])
                if wks_len > 0:
                    self.workstation = blob[wks_offset:wks_offset + wks_len]
                    self.workstation = self.workstation.decode('UTF-16')

        if self.username is None:
            raise ServerException('User name not found in request NTLM authorization token')


    def fetch_ldb_record(self, ldb_filter):
        """
        Fetchs a record from LDB
        """
        samdb = config["samba"]["samdb_ldb"]
        base_dn = config["samba"]["domaindn"]
        res = samdb.search(base=base_dn, scope=ldb.SCOPE_SUBTREE,
                           expression=ldb_filter, attrs=["*"])
        if len(res) == 1:
            ldb_record = res[0]
        else:
            raise DbException('Error fetching database entry. Expected one result but got %s' % len(res))

        return ldb_record

    def check_mailbox(self, request_mailbox):
        """
        Checks that the mailbox specified in the request belongs to the user
        who is making the request
        """
        user_ldb_record = self.fetch_ldb_record("(&(objectClass=user)(samAccountName=%s))" % self.username)
        mbox_ldb_record = self.fetch_ldb_record("(&(objectClass=user)(mail=%s))" % request_mailbox)

        user_sid = user_ldb_record['objectSID'][0]
        mbox_sid = mbox_ldb_record['objectSID'][0]

        if user_sid == mbox_sid:
            return True

        samdb = config["samba"]["samdb_ldb"]
        # ID of the user who is making the request
        user_sid = samdb.schema_format_value('objectSid', user_sid)
        # Mailbox ID of the mailbox for which the attempt was made
        mbox_sid = samdb.schema_format_value('objectSid', mbox_sid)
        raise AccessDeniedException('Microsoft.Exchange.Data.Storage.AccessDeniedException: '
                                    'User is not mailbox owner. User = %s, MailboxGuid = %s '
                                    '---> User is not mailbox owner.' % (user_sid, mbox_sid))

    def process(self, request):
        """
        Process SOAP request
        """
        if request.body is not None and len(request.body) > 0:
            body = urllib.unquote_plus(request.body)
            tree = ElementTree(file=StringIO(body))
            envelope = tree.getroot()
            if envelope is None:
                raise InvalidRequestException('Invalid syntax')
            body = envelope.find("q:Body", namespaces = namespaces)
            if body is None:
                raise InvalidRequestException('Invalid syntax')

            soap_req = body.find("m:GetUserOofSettingsRequest", namespaces=namespaces)
            if soap_req is not None:
                return self.process_get_request(soap_req)

            soap_req = body.find("m:SetUserOofSettingsRequest", namespaces=namespaces)
            if soap_req is not None:
                return self.process_set_request(soap_req)

            raise InvalidRequestException('Unknown SOAP request')

        raise InvalidRequestException('No body in request')

    def _header_element(self):
        header_element = Element("{%s}Header" % namespaces['q'])

        ServerVersionInfo = Element("{%s}ServerVersionInfo" % namespaces['t'])
        ServerVersionInfo.set('MajorVersion', '8')
        ServerVersionInfo.set('MinorVersion', '1')
        ServerVersionInfo.set('MajorBuildNumber', '240')
        ServerVersionInfo.set('MinorBuildNumber', '5')
        header_element.append(ServerVersionInfo)
        return header_element

    def _body_element(self):
        return Element("{%s}Body" % namespaces['q'])

    def _fault_element_from_exception(self, e):
        fault_element = Element("{%s}Fault" % namespaces['q'])
        fault_code_element = Element("FaultCode")
        if isinstance(e, AccessDeniedException):
            fault_code_element.text = "Client"
        else:
            fault_code_element.text = "Server"
            fault_element.append(fault_code_element)

            fault_string_element = Element("FaultString")
            fault_string_element.text = e
            fault_element.append(fault_string_element)

            fault_actor_element = Element("FaultActor")
            fault_actor_element.text = self.workstation
            fault_element.append(fault_actor_element)

            fault_detail_element = Element("Detail")
            fault_element.append(fault_detail_element)
            if isinstance(e, AccessDeniedException):
                error_code_element = Element("{%s}ErrorCode" % namespaces['m'])
                error_code_element.text = "-2146233088"
                fault_detail_element.append(error_code_element)
        return fault_element

    def _address_from_request(self, elem):
        mailbox_element = elem.find("t:Mailbox", namespaces=namespaces)
        address_element = mailbox_element.find("t:Address", namespaces=namespaces)
        return address_element.text

    def _response_string(self,  envelope_element):
        response_string = "<?xml version='1.0' encoding='utf-8'?>\n"
        response_string += tostring(envelope_element, encoding='utf-8', method='xml')
        return response_string

    def process_get_request(self, elem):
        # Prepare the response
        envelope_element = Element("{%s}Envelope" % namespaces['q'])

        header_element = self._header_element()
        envelope_element.append(header_element)

        body_element = self._body_element()
        envelope_element.append(body_element)

        # Check that the mailbox specified in the request belong to the user
        # who is making the request
        mailbox = self._address_from_request(elem)
        try:
            self.check_mailbox(mailbox)
        except Exception as e:
            fault_element = self._fault_element_from_exception(e)
            body_element.append(fault_element)
            return self._response_string(envelope_element)

        # Retrieve OOF settings
        oof = OofSettings()
        oof.from_sieve(mailbox)

        # Build the command response
        response_element = Element("{%s}GetUserOofSettingsResponse" % namespaces['m'])
        body_element.append(response_element)

        # Attach info to response
        response_message_element = Element("ResponseMessage")
        response_message_element.set('ResponseClass', 'Success')
        response_element.append(response_message_element)

        response_code_element = Element("ResponseCode")
        response_code_element.text = "NoError"
        response_message_element.append(response_code_element)

        oof_settings_element = Element("{%s}OofSettings" % namespaces['t'])
        response_element.append(oof_settings_element)

        oof.to_xml(oof_settings_element, response_element)

        return self._response_string(envelope_element)

    def process_set_request(self, elem):
        # Prepare the response
        envelope_element = Element("{%s}Envelope" % namespaces['q'])

        header_element = self._header_element()
        envelope_element.append(header_element)

        body_element = self._body_element()
        envelope_element.append(body_element)

        # Check that the mailbox specified in the request belong to the user
        # who is making the request
        mailbox = self._address_from_request(elem)
        try:
            self.check_mailbox(mailbox)
        except Exception as e:
            fault_element = self._fault_element_from_exception(e)
            body_element.append(fault_element)
            return self._response_string(envelope_element)

        settings_element = elem.find("t:UserOofSettings", namespaces=namespaces)
        allow_external_element = elem.find("t:AllowExternalOof", namespaces=namespaces)

        # Set settings
        oof = OofSettings()
        oof.from_xml(settings_element, allow_external_element)
        oof.to_sieve(mailbox)

        response_element = Element("{%s}SetUserOofSettingsResponse" % namespaces['m'])
        body_element.append(response_element)

        response_message_element = Element("ResponseMessage")
        response_message_element.set('ResponseClass', 'Success')
        response_element.append(response_message_element)

        response_code_element = Element("ResponseCode")
        response_code_element.text = "NoError"
        response_message_element.append(response_code_element)
        return self._response_string(envelope_element)


class OofSettings:
    def __init__(self):
        self._sieve_script_header = "# OpenChange OOF script\n"
        self._config = {}
        self._config['state'] = None
        self._config['external_audience'] = None
        self._config['duration_start_time'] = None
        self._config['duration_end_time'] = None
        self._config['internal_reply_message'] = None
        self._config['external_reply_message'] = None
        self._config['allow_external_oof'] = None

    def _sieve_path(self, mailbox):
        ebox_uid = 107
        ebox_gid = 112
        sieve_path_base = '/var/vmail/sieve'
        sieve_path_vdomain = os.path.join(sieve_path_base, mailbox.split('@')[1])
        sieve_path_mailbox = os.path.join(sieve_path_vdomain, mailbox)
        sieve_path_script = os.path.join(sieve_path_mailbox, 'sieve-script')
        sieve_path_backup = None

        if not os.path.isdir(sieve_path_base):
            raise Exception("Sieve path base dir not exists")

        if not os.path.isdir(sieve_path_vdomain):
            os.mkdir(sieve_path_vdomain, 0770)
            os.chown(sieve_path_vdomain, ebox_uid, ebox_gid)

        if not os.path.isdir(sieve_path_mailbox):
            os.mkdir(sieve_path_mailbox, 0770)
            os.chown(sieve_path_mailbox, ebox_uid, ebox_gid)

        if os.path.isfile(sieve_path_script):
            if not self._isOofScript(sieve_path_script):
                sieve_path_backup = sieve_path_script + '.user'
                shutil.copyfile(sieve_path_script, bakup)
                shutil.copystat(sieve_path_script, bakup)
        elif os.path.exists(sieve_path_script):
            raise Exception(sieve_path_script + " exists and it is not a regular file")

        return (sieve_path_script, sieve_path_backup)

    def _isOofScript(self, path):
        f = open(path, 'r')
        line = f.readline()
        return line == self._sieve_script_header

    def _to_json(self):
        return json.dumps(self._config)

    def from_sieve(self, mailbox):
        """
        Loads OOF settings for specified mailbox
        """
        (path, ignore) = self._sieve_path(mailbox)
        if os.path.isfile(path):
            f = open(path, 'r')
            line = f.readline()
            line = f.readline()
            line = f.readline()
            line = f.readline()
            self._config = json.loads(line)
        else:
            # Default settings
            self._config['state'] = 'Disabled'
            self._config['external_audience'] = 'All'
            self._config['duration_start_time'] = '1970-01-01T00:00:00Z'
            self._config['duration_end_time'] = '2099-12-12T00:00:00Z'
            self._config['internal_reply_message'] = base64.b64encode('I am out of office.')
            self._config['external_reply_message'] = base64.b64encode('I am out of office.')
            self._config['allow_external_oof'] = 'All'

    def to_sieve(self, mailbox):
        (sieve_path_script, sieve_path_include) = self._sieve_path(mailbox)
        template = u"""$header\n/*\n$config\n*/\n\n require ["date","relational","vacation"];\n\n"""

        if self._config['duration_start_time'] is not None and self._config['duration_end_time'] is not None:
            template += """if allof(currentdate :value "ge" "date" "$start", currentdate :value "le" "date" "$end")\n {"""

        template += """vacation  :days 1 :subject "$subject" "$message";\n"""
        if self._config['duration_start_time'] is not None and self._config['duration_end_time'] is not None:
            template += """}\n\n"""

        message = ''
        message += '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">\n'
        message += base64.b64decode(self._config['external_reply_message'])
        message = message.replace('"', '\\"')
        message = message.replace(';', '\\;')

        script = string.Template(template).substitute(
            header = self._sieve_script_header,
            config = self._to_json(),
            start = self._config['duration_start_time'],
            end = self._config['duration_end_time'],
            subject = "Out of office automatic reply",
            message = message
        )

        if sieve_path_include is not None:
            script += "\n" + 'include :personal "' + include + '";'
            script += "\n"

        f = open(sieve_path_script, 'w')
        f.write(script.encode('utf8'))
        f.close()
        os.chmod(sieve_path_script, 0770)

    def from_xml(self, settings_element, allow_external_element=None):
        """
        Load settings from XML root element
        """
        oof_state_element = settings_element.find('t:OofState', namespaces=namespaces)
        if oof_state_element is not None:
            self._config['state'] = oof_state_element.text

        external_audience_element = settings_element.find('t:ExternalAudience', namespaces=namespaces)
        if external_audience_element is not None:
            self._config['external_audience'] = external_audience_element.text

        duration_element = settings_element.find('t:Duration', namespaces=namespaces)
        if duration_element is not None:
            start_time_element = duration_element.find('t:StartTime', namespaces=namespaces)
            if start_time_element is not None:
                self._config['duration_start_time'] = start_time_element.text.split('T')[0]

            end_time_element = duration_element.find('t:EndTime', namespaces=namespaces)
            if end_time_element is not None:
                self._config['duration_end_time'] = end_time_element.text.split('T')[0]

        internal_reply_element = settings_element.find('t:InternalReply', namespaces=namespaces)
        if internal_reply_element is not None:
            message_element = internal_reply_element.find('t:Message', namespaces=namespaces)
            if message_element is not None:
                # Strip the BOM from the beginning of the Unicode string
                text = message_element.text
                text = text.lstrip(unicode(codecs.BOM_UTF16_LE, "UTF-16LE"))
                self._config['internal_reply_message'] = base64.b64encode(text)

        external_reply_element = settings_element.find('t:ExternalReply', namespaces=namespaces)
        if external_reply_element is not None:
            message_element = external_reply_element.find('t:Message', namespaces=namespaces)
            if message_element is not None:
                # Strip the BOM from the beginning of the Unicode string
                text = message_element.text
                text = text.lstrip(unicode(codecs.BOM_UTF16_LE, "UTF-16LE"))
                self._config['external_reply_message'] = base64.b64encode(text)

        if allow_external_element is not None:
            self._config['allow_external_oof'] = allow_external_element.text

    def to_xml(self, oof_settings_element, response_element):
        """
        Fill the XML root element with OOF settings
        """
        if self._config['state'] is not None:
            oof_state_element = Element('OofState')
            oof_state_element.text = self._config['state']
            oof_settings_element.append(oof_state_element)

        if self._config['external_audience'] is not None:
            external_audience_element = Element("ExternalAudience")
            external_audience_element.text = self._config['external_audience']
            oof_settings_element.append(external_audience_element)

        duration_element = Element("Duration")
        oof_settings_element.append(duration_element)

        if self._config['duration_start_time'] is not None:
            StartTime = Element("StartTime")
            StartTime.text = self._config['duration_start_time']
            duration_element.append(StartTime)

        if self._config['duration_end_time'] is not None:
            EndTime = Element("EndTime")
            EndTime.text = self._config['duration_end_time']
            duration_element.append(EndTime)

        InternalReply = Element("InternalReply")
        oof_settings_element.append(InternalReply)

        if self._config['internal_reply_message'] is not None:
            MessageInternal = Element("Message")
            MessageInternal.text = base64.b64decode(self._config['internal_reply_message'])
            InternalReply.append(MessageInternal)

        ExternalReply = Element("ExternalReply")
        oof_settings_element.append(ExternalReply)

        if self._config['external_reply_message'] is not None:
            MessageExternal = Element("Message")
            MessageExternal.text = base64.b64decode(self._config['external_reply_message'])
            ExternalReply.append(MessageExternal)

        #if self._config['allow_external_oof'] is not None:
            AllowExternalOof = Element("AllowExternalOof")
            AllowExternalOof.text = 'All' #self._config['allow_external_oof']
            response_element.append(AllowExternalOof)

class OofController(BaseController):
    """The constroller class for OutOfOffice requests."""

    @restrict('POST')
    def index(self, **kwargs):
        try:
            if "environ" in kwargs:
                environ = kwargs["environ"]
            else:
                environ = None

            rqh = OofHandler(environ)
            response.headers["content-type"] = "text/xml"
            body = rqh.process(request)
        except:
            response.status = 500
            response.headers["content-type"] = "text/plain"
            # TODO: disable the showing of exception in prod
            log.error(traceback.format_exc())
            body = "An exception occurred:\n" + traceback.format_exc()

        return body
