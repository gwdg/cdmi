# Copyright (c) 2011 IBM
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from cdmibase import \
    (Consts, Controller, concat_parts)
from cdmiutils import \
    (get_pair_from_header, get_err_response, check_resource)
from webob import Request, Response
from swift.common.utils import get_logger
import json


class CDMIBaseController(Controller):
    """
    Handles container request.
    This is the base class for other controllers. In the constructor,
    it sets up new path for handing the request to OS and also set up
    the parent_name, metadata_prefix according to OS structure. This base
    class defines few more utility methods to process metadata and check
    parent status according to the request path.
    """
    def __init__(self, env, conf, app, logger, account_name, container_name,
                 parent_name, object_name, **kwargs):
        Controller.__init__(self, conf, app, logger)
        self.account_name = account_name
        self.container_name = container_name
        self.object_name = object_name
        self.parent_name = parent_name
        if self.object_name:
            self.metadata_prefix = Consts.META_OBJECT_ID
        else:
            self.metadata_prefix = Consts.META_CONTAINER_ID
        env['PATH_INFO'] = '/v1/' + concat_parts(self.account_name,
                                                 self.container_name,
                                                 self.parent_name,
                                                 self.object_name)

    def _process_metadata(self, headers):
        """ Get CDMI metadata from the header and add to the body """
        metadata = {}
        for header, value in headers.iteritems():
            key = header.lower()
            if key.startswith(self.metadata_prefix):
                key, value = get_pair_from_header(value)
                if key != '' and value != '':
                    metadata[key] = value

        return metadata

    def _check_parent(self, env, start_response):
        """
        This method checks if the parent really represents a directory.
        Returns error if parent does not exist or the parent actually points
        to a non directory. Returns None means that the parent points to a
        valid container (top container or virtual container)
        """
        if self.parent_name:
            # Try to hit the resource url and see if it exists
            path = '/' + concat_parts('v1', self.account_name,
                                      self.container_name, self.parent_name)
            exists, headers, dummy = check_resource(env, 'HEAD',
                                                    path, self.logger)
            if exists:
                content_type = str(headers.get('content-type', ''))
                if content_type.find('application/directory') < 0:
                    return get_err_response('InvalidContainerName')
                else:
                    return None
            else:
                # Check if there is anything below that parent, if it is,
                # then this is actually a virtual container.
                path = '/' + concat_parts('v1', self.account_name,
                                          self.container_name)
                query_string = 'delimiter=/&prefix=' + self.parent_name + '/'
                parent_exists, dummy, body = check_resource(env, 'GET', path,
                                                            self.logger, True,
                                                            query_string)
                if parent_exists:
                    try:
                        children = json.loads(body)
                        if len(children) <= 0:
                            # No children under, no resource exist
                            return get_err_response('NoParentContainer')
                        else:
                            return None
                    except ValueError:
                        return get_err_response('InconsistantState')
                # The root container does not event exist, this is an error
                else:
                    return get_err_response('NoParentContainer')

        return None

    def _check_resource_attribute(self, env, start_response):
        """
        This method checks if a given url points to either a container, or
        an object or does not exist. It will also check if a resource is a
        virtual container in CDMI term. If a resource exists, the headers
        will also be return in following sequence.
        res - The response which containers errors, None means there is no
        error
        is_container - if it is a container, it is True, otherwise, it is
        False
        headers - if the resource exists, this holds the headers
        children - if it is a container, return container's child list
        """
        path = env['PATH_INFO']
        res, is_container, headers, children = None, False, {}, None
        exists, headers, dummy = check_resource(env, 'HEAD', path, self.logger)
        # If exists, we need to check if the resource is a container
        if exists:
            content_type = (headers.get('content-type') or '').lower()
            if (content_type.find('application/directory') < 0 and
                self.object_name):
                is_container = False
            else:
                is_container = True
        # None self.object_name means that we are dealing with a real OS
        # container, return resource not found error
        elif not self.object_name:
            res = get_err_response('NoSuchKey')

        if res is None and (not exists or is_container):
            # Now we will try to get the children of the container and also
            # do more checks to see if there is any virtual resources.
            path = '/' + concat_parts('v1', self.account_name,
                                      self.container_name)
            query_string = 'delimiter=/'
            if self.object_name:
                query_string += ('&prefix=' +
                                 concat_parts(self.parent_name,
                                              self.object_name) +
                                 '/')

            container_exists, dummy, body = check_resource(env, 'GET', path,
                                                           self.logger, True,
                                                           query_string)
            if container_exists:
                try:
                    children = json.loads(body)
                    no_of_children = len(children)
                    # The entity could be a virtual container since it
                    # does not exist
                    if not exists:
                        # There is no children under also not exists,
                        # it is not virtual container.
                        if no_of_children <= 0:
                            res = get_err_response('NoSuchKey')
                        # There are children under and not exist, it is
                        # a virtual container
                        elif no_of_children > 0 and not exists:
                            is_container = True
                except ValueError:
                    res = get_err_response('InconsistantState')
            else:
                res = get_err_response('NoSuchKey')

        self.logger.info('is_container=' + str(is_container))
        self.logger.info(res)
        return res, is_container, headers, children


class CDMICommonController(CDMIBaseController):
    """
    Handles container request.
    This controller handles delete, get and capability requests for both
    container and objects because there is no identifier to indicate if
    a delete/capability request is for container or object, so for all
    delete/capability request, this control will be handling the request.
    Other than the delete, capability request, this controller will also
    handle container creation, update and retrieval requests.
    """

    def _capability(self, env, start_response):

        res, is_container, headers, children = \
            self._check_resource_attribute(env, start_response)

        if res:
            return res

        res = Response()
        res.status = 200
        res.headers['Content-Type'] = Consts.CDMI_APP_CAPABILITY
        res.headers[Consts.CDMI_VERSION] = Consts.CDMI_VERSION_VALUE

        body = {}
        body['objectType'] = Consts.CDMI_APP_CAPABILITY

        if self.object_name:
            body['parentURI'] = concat_parts(self.cdmi_capability_id,
                                             self.account_name,
                                             self.container_name,
                                             self.parent_name) + '/'
        else:
            body['parentURI'] = concat_parts(self.cdmi_capability_id,
                                             self.account_name) + '/'

        body['capabilities'] = {}
        if is_container:
            if self.object_name:
                body['objectName'] = self.object_name + '/'
            else:
                body['objectName'] = self.container_name + '/'

            body['capabilities']['cdmi_list_children'] = True
            body['capabilities']['cdmi_read_metadata'] = True
            body['capabilities']['cdmi_modify_metadata'] = True
            body['capabilities']['cdmi_create_dataobject'] = True
            body['capabilities']['cdmi_delete_container'] = True
            body['capabilities']['cdmi_create_container'] = True
        else:
            body['objectName'] = self.object_name
            body['capabilities']['cdmi_read_value'] = True
            body['capabilities']['cdmi_read_metadata'] = True
            body['capabilities']['cdmi_modify_value'] = True
            body['capabilities']['cdmi_modify_metadata'] = True
            body['capabilities']['cdmi_delete_dataobject'] = True

        res.body = json.dumps(body, indent=2)
        return res

    def _read_object(self, env, start_response, headers):

        req = Request(env)
        os_res = req.get_response(self.app)

        cdmi_version = env.get('HTTP_X_CDMI_SPECIFICATION_VERSION', False)
        # If this is not a CDMI content request, simply return the response
        if not cdmi_version:
            return os_res

        # For CDMI content request, more work need to be done.
        res = Response()
        # Set up CDMI required headers
        res.headers[Consts.CDMI_VERSION] = Consts.CDMI_VERSION_VALUE
        res.headers['Content-Type'] = Consts.CDMI_APP_OBJECT

        object_body = os_res.body
        # Build the response message body according to CDMI specification
        body = {}

        # Setup required attributes for response body
        body['objectType'] = Consts.CDMI_APP_OBJECT
        body['objectName'] = self.object_name
        body['parentURI'] = concat_parts(self.account_name,
                                         self.parent_name) + '/'
        body['capabilitiesURI'] = concat_parts(self.cdmi_capability_id,
                                               self.account_name,
                                               self.container_name,
                                               self.parent_name,
                                               self.object_name) + '/'
        body['completionStatus'] = 'Complete'
        body['metadata'] = {}

        # Handling CDMI metadata
        body['metadata'] = self._process_metadata(headers)
        body['mimetype'] = headers.get('content-type', '')
        body['valuetransferencoding'] = \
            headers.get(Consts.VALUE_ENCODING, 'utf-8')
        body['valuerange'] = '0-' + str(len(object_body))
        body['value'] = object_body
        res.body = json.dumps(body, indent=2)
        res.status_int = 200

        return res

    def _read_container(self, env, start_response, headers, children):

        # Build the response message body according to CDMI specification
        res = Response()
        res.headers['content-type'] = 'application/json; charset=UTF-8'

        body = {}

        # Setup required attributes for response body
        body['objectType'] = Consts.CDMI_APP_CONTAINER
        if self.object_name:
            body['objectName'] = self.object_name + '/'
            body['parentURI'] = concat_parts(self.account_name,
                                             self.container_name,
                                             self.parent_name) + '/'
        else:
            body['objectName'] = self.container_name + '/'
            body['parentURI'] = self.account_name + '/'

        body['capabilitiesURI'] = concat_parts(self.cdmi_capability_id,
                                               self.account_name,
                                               self.container_name,
                                               self.parent_name,
                                               self.object_name) + '/'
        body['completionStatus'] = 'Complete'
        body['metadata'] = {}

        #Get CDMI metadata from the header and add to the body
        for header, value in headers.iteritems():
            key = header.lower()
            if key.startswith(self.metadata_prefix):
                key, value = get_pair_from_header(value)
                if key != '' and value != '':
                    body['metadata'][key] = value

        body['children'] = []
        if children:
            string_to_cut = concat_parts(self.parent_name, self.object_name)
            size = len(string_to_cut)
            if size > 0:
                size += 1
            tracking_device = {}
            for child in children:
                if child.get('name', False):
                    child_name = child.get('name')
                else:
                    child_name = child.get('subdir', False)
                if child_name:
                    child_name = child_name[size:]
                    if not child_name.endswith('/'):
                        content_type = child.get('content_type', '')
                        if content_type.find('directory') >= 0:
                            child_name += '/'
                    if tracking_device.get(child_name) is None:
                        tracking_device[child_name] = child_name
                        body['children'].append(child_name)

        res.body = json.dumps(body, indent=2)
        res.status_int = 200

        return res

    def _read_entity(self, env, start_response):
        res, is_container, headers, children = \
            self._check_resource_attribute(env, start_response)

        if res is None:
            if ((is_container and not env.get('X-WANTS-CONTAINER')) or
                (not is_container and env.get('X-WANTS-CONTAINER'))):
                return get_err_response('Conflict')

            if is_container:
                return self._read_container(env, start_response,
                                            headers, children)
            else:
                return self._read_object(env, start_response, headers)
        else:
            return res

    # Use GET to handle all container read related operations.
    # TODO: filtering resources
    def GET(self, env, start_response):
        """
        Handle GET Container (List Objects) request
        """
        # Create a new WebOb Request object according to the current request
        accept_header = env.get('HTTP_ACCEPT', '')
        if accept_header.find(Consts.CDMI_APP_CAPABILITY) >= 0:
            return self._capability(env, start_response)
        else:
            return self._read_entity(env, start_response)

    def DELETE(self, env, start_response):
        """
        Handle DELETE both container and data object removal.
        """

        path = '/v1/' + self.account_name + '/' + self.container_name
        query_string = 'delimiter=/'
        if self.object_name:
            query_string += '&prefix=' + concat_parts(self.parent_name,
                                                      self.object_name) + '/'
        exists, dummy, body = check_resource(env, 'GET', path, self.logger,
                                             True, query_string)
        # Not even the top container exist, so there is no such resource.
        if not exists:
            return get_err_response('NoSuchKey')
        # Top container exists, check if there is anything under.
        else:
            try:
                children = json.loads(body)
                #there are some children under
                if len(children) > 0:
                    return get_err_response('ContainerNotEmpty')
            except ValueError:
                return get_err_response('InconsistantState')

        # Create a new WebOb Request object according to the current request
        req = Request(env)
        # Now send the request over.
        return req.get_response(self.app)