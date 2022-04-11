# Copyright (c) Regents of the University of Minnesota
# Distributed under the terms of the Modified BSD License.

"""WrapSpawner classes

WrapSpawner provides a mechanism to wrap the interface of a Spawner such that
the Spawner class to use for single-user servers can be chosen dynamically.
The child Spawner is created and started using the same logic as in User.spawn(),
but not until start() or load_state() is called. Thus, subclasses may modify the
class or properties of the child Spawner at any earlier point (e.g. from
Authenticator pre_spawn hooks or options form processing).

Note that there is no straightforward way to simply save the child_class Type
value in the ORM, so a subclass that provides a way to change the child_class
must also arrange to reproduce that change when state is reloaded.

Provided as an initial application is ProfilesSpawner, which accepts a list
of provided Spawner configurations, and generates an options form from that list.
Using this mechanism, the administrator can provide users with a pre-approved
selection of Spawner configurations.
"""

import os
import json
import re
import urllib.request

from tornado import gen, concurrent

from jupyterhub.spawner import LocalProcessSpawner, Spawner
from traitlets import (
    Instance, Type, Tuple, List, Dict, Integer, Unicode, Float, Any
)
from traitlets import directional_link

# Only needed for DockerProfilesSpawner
try:
    import docker
except ImportError:
    pass


# Utility to create dummy Futures to return values through yields
def _yield_val(x=None):
    f = concurrent.Future()
    f.set_result(x)
    return f


class WrapSpawner(Spawner):
    # Grab this from constructor args in case some Spawner ever wants it
    config = Any()

    child_class = Type(LocalProcessSpawner, Spawner,
                       config=True,
                       help="""The class to wrap for spawning single-user servers.
                Should be a subclass of Spawner.
                """
                       )

    child_config = Dict(default_value={},
                        config=True,
                        help="Dictionary of config values to apply to wrapped spawner class."
                        )

    child_state = Dict(default_value={})

    child_spawner = Instance(Spawner, allow_none=True)

    def construct_child(self):
        if self.child_spawner is None:
            self.child_spawner = self.child_class(
                user=self.user,
                db=self.db,
                hub=self.hub,
                authenticator=self.authenticator,
                oauth_client_id=self.oauth_client_id,
                server=self._server,
                config=self.config,
                **self.child_config
            )
            # initial state will always be wrong since it will see *our* state
            self.child_spawner.clear_state()
            if self.child_state:
                self.child_spawner.load_state(self.child_state)

            # link traits common between self and child
            common_traits = (
                    set(self.trait_names()) &
                    set(self.child_spawner.trait_names()) -
                    set(self.child_config.keys())
            )
            for trait in common_traits:
                directional_link((self, trait), (self.child_spawner, trait))
        return self.child_spawner

    def load_child_class(self, state):
        # Subclasses must arrange for correct child_class setting from load_state
        pass

    def load_state(self, state):
        super().load_state(state)
        self.load_child_class(state)
        self.child_config.update(state.get('child_conf', {}))
        self.child_state = state.get('child_state', {})
        self.construct_child()

    def get_state(self):
        state = super().get_state()
        state['child_conf'] = self.child_config
        if self.child_spawner:
            self.child_state = state['child_state'] = self.child_spawner.get_state()
        return state

    def clear_state(self):
        super().clear_state()
        if self.child_spawner:
            self.child_spawner.clear_state()
        self.child_state = {}
        self.child_config = {}
        self.child_spawner = None

    # proxy functions for start/poll/stop
    # pass back the child's Future, or create a dummy if needed

    def start(self):
        if not self.child_spawner:
            self.construct_child()
        return self.child_spawner.start()

    def stop(self, now=False):
        if self.child_spawner:
            return self.child_spawner.stop(now)
        else:
            return _yield_val()

    def poll(self):
        if self.child_spawner:
            return self.child_spawner.poll()
        else:
            return _yield_val(1)

    if hasattr(Spawner, 'progress'):
        @property
        def progress(self):
            if self.child_spawner:
                return self.child_spawner.progress
            else:
                raise RuntimeError("No child spawner yet exists - can not get progress yet")


class ProfilesSpawner(WrapSpawner):
    """ProfilesSpawner - leverages the Spawner options form feature to allow user-driven
        configuration of Spawner classes while permitting:
        1) configuration of Spawner classes that don't natively implement options_form
        2) administrator control of allowed configuration changes
        3) runtime choice of which Spawner backend to launch
    """

    profiles = List(
        trait=Tuple(Unicode(), Unicode(), Type(Spawner), Dict()),
        default_value=[('Local Notebook Server', 'local', LocalProcessSpawner,
                        {'start_timeout': 15, 'http_timeout': 10})],
        minlen=1,
        config=True,
        help="""List of profiles to offer for selection. Signature is:
            List(Tuple( Unicode, Unicode, Type(Spawner), Dict )) corresponding to
            profile display name, unique key, Spawner class, dictionary of spawner config options.

            The first three values will be exposed in the input_template as {display}, {key}, and {type}"""
    )

    child_profile = Unicode()

    form_template = Unicode(
        """<label for="profile">Select a job profile:</label>
        <select class="form-control" name="profile" required autofocus>
        {input_template}
        </select>
        <textfield></textfield>
        """,
        config=True,
        help="""Template to use to construct options_form text. {input_template} is replaced with
            the result of formatting input_template against each item in the profiles list."""
    )

    first_template = Unicode('selected',
                             config=True,
                             help="Text to substitute as {first} in input_template"
                             )

    input_template = Unicode("""
        <option value="{key}" {first}>{display}</option>""",
                             config=True,
                             help="""Template to construct {input_template} in form_template. This text will be formatted
            against each item in the profiles list, in order, using the following key names:
            ( display, key, type ) for the first three items in the tuple, and additionally
            first = "checked" (taken from first_template) for the first item in the list, so that
            the first item starts selected."""
                             )

    options_form = Unicode()

    def _options_form_default(self):
        temp_keys = [dict(display=p[0], key=p[1], type=p[2], first='') for p in self.profiles]
        temp_keys[0]['first'] = self.first_template
        text = ''.join([self.input_template.format(**tk) for tk in temp_keys])
        return self.form_template.format(input_template=text)

    def options_from_form(self, formdata):
        # Default to first profile if somehow none is provided
        return dict(profile=formdata.get('profile', [self.profiles[0][1]])[0])

    # load/get/clear : save/restore child_profile (and on load, use it to update child class/config)

    def select_profile(self, profile):
        # Select matching profile, or do nothing (leaving previous or default config in place)
        for p in self.profiles:
            if p[1] == profile:
                self.child_class = p[2]
                self.child_config = p[3]
                break

    def construct_child(self):
        self.child_profile = self.user_options.get('profile', "")
        self.select_profile(self.child_profile)
        super().construct_child()

    def load_child_class(self, state):
        try:
            self.child_profile = state['profile']
        except KeyError:
            self.child_profile = ''
        self.select_profile(self.child_profile)

    def get_state(self):
        state = super().get_state()
        state['profile'] = self.child_profile
        return state

    def clear_state(self):
        super().clear_state()
        self.child_profile = ''


class DockerProfilesSpawner(ProfilesSpawner):
    """DockerProfilesSpawner - leverages ProfilesSpawner to dynamically create DockerSpawner
        profiles dynamically by looking for docker images that end with "jupyterhub". Due to the
        profiles being dynamic the "profiles" config item from the ProfilesSpawner is renamed as
        "default_profiles". Please note that the "docker" and DockerSpawner packages are required
        for this spawner to work.
    """

    default_profiles = List(
        trait=Tuple(Unicode(), Unicode(), Type(Spawner), Dict()),
        default_value=[],
        config=True,
        help="""List of profiles to offer in addition to docker images for selection. Signature is:
            List(Tuple( Unicode, Unicode, Type(Spawner), Dict )) corresponding to
            profile display name, unique key, Spawner class, dictionary of spawner config options.

            The first three values will be exposed in the input_template as {display}, {key}, and {type}"""
    )

    docker_spawner_args = Dict(
        default_value={},
        config=True,
        help="Args to pass to DockerSpawner."
    )

    jupyterhub_docker_tag_re = re.compile('^.*jupyterhub$')

    def _nvidia_args(self):
        try:
            resp = urllib.request.urlopen('http://localhost:3476/v1.0/docker/cli/json')
            body = resp.read().decode('utf-8')
            args = json.loads(body)
            return dict(
                read_only_volumes={vol.split(':')[0]: vol.split(':')[1] for vol in args['Volumes']},
                extra_create_kwargs={"volume_driver": args['VolumeDriver']},
                extra_host_config={"devices": args['Devices']},
            )
        except urllib.error.URLError:
            return {}

    def _docker_profile(self, nvidia_args, image):
        spawner_args = dict(container_image=image, network_name=self.user.name)
        spawner_args.update(self.docker_spawner_args)
        spawner_args.update(nvidia_args)
        nvidia_enabled = "w/GPU" if len(nvidia_args) > 0 else "no GPU"
        return ("Docker: (%s): %s" % (nvidia_enabled, image), "docker-%s" % (image), "dockerspawner.SystemUserSpawner",
                spawner_args)

    def _jupyterhub_docker_tags(self):
        try:
            include_jh_tags = lambda tag: self.jupyterhub_docker_tag_re.match(tag)
            return filter(include_jh_tags, [tag for image in docker.from_env().images.list() for tag in image.tags])
        except NameError:
            raise Exception('The docker package is not installed and is a dependency for DockerProfilesSpawner')

    def _docker_profiles(self):
        return [self._docker_profile(self._nvidia_args(), tag) for tag in self._jupyterhub_docker_tags()]

    @property
    def profiles(self):
        return self.default_profiles + self._docker_profiles()

    @property
    def options_form(self):
        temp_keys = [dict(display=p[0], key=p[1], type=p[2], first='') for p in self.profiles]
        temp_keys[0]['first'] = self.first_template
        text = ''.join([self.input_template.format(**tk) for tk in temp_keys])
        return self.form_template.format(input_template=text)


class CustomSpawner(WrapSpawner):
    """CustomSpawner allows batch commands to be displayed and customized
    before launch by a user via a HTML textfield.
    These commands are divided into two categories: System and User.
    System commands are available to all users, and User
    commands are stored in the specific user's home directory. The filepaths
    for these are set in the jupyterhub config by the following variables:
    c.CustomSpawner.system_profile_path
    c.CustomSpawner.user_profile_path
    """

    profiles = List(
        trait=Tuple(Unicode(), Unicode(), Type(Spawner), Dict(), Unicode()),
        default_value=[('Local Notebook Server', 'local', LocalProcessSpawner,
                        {'start_timeout': 15, 'http_timeout': 10}, "")],
        minlen=0,
        config=True,
        help="""List of profiles to offer for selection. Signature is:
            List(Tuple( Unicode, Unicode, Type(Spawner), Dict )) corresponding to
            profile display name, unique key, Spawner class, dictionary of spawner config options.

            The first three values will be exposed in the input_template as {display}, {key}, and {type}"""
    )

    child_profile = Unicode()

    empty_form_template = Unicode("""
    No batch profiles available.
     <script type="application/javascript">
         window.onload = function(){
             var form = document.getElementById("spawn_form");
             var elements = form.elements;
             for (var i = 0, len = elements.length; i < len; ++i) {
                 elements[i].disabled = true
             }
         }
</script>  
     """)

    form_template = Unicode(
        """
         <script type="application/javascript">
         {batch_commands}
         {select_handler}
         </script>
        <label for="profile">Select a job profile:</label>
        <select onchange = "profile_selected()" id = "profile_selector" class="form-control" name="profile" required autofocus>
        {input_template}
        </select>
        <textarea style ="height: 400px;"class="form-control" id="batch_command" name="batch_command">{batch_command}</textarea>
        """,
        config=True,
        help="""Template to use to construct options_form text. {input_template} is replaced with
            the result of formatting input_template against each item in the profiles list."""
    )

    first_template = Unicode('selected',
                             config=True,
                             help="Text to substitute as {first} in input_template"
                             )

    input_template = Unicode("""
        <option id="{id}" value="{key}" {first}>{display}</option>""",
                             config=True,
                             help="""Template to construct {input_template} in form_template. This text will be formatted
            against each item in the profiles list, in order, using the following key names:
            ( display, key, type ) for the first three items in the tuple, and additionally
            first = "checked" (taken from first_template) for the first item in the list, so that
            the first item starts selected."""
                             )

    options_form = Unicode()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if 'spawner_class' in kwargs['config']['CustomSpawner']:
            self.spawner_class = kwargs['config']['CustomSpawner']['spawner_class']
            pass
        else:
            self.spawner_class = 'SlurmSpawner'

        if 'system_profile_path' in kwargs['config']['CustomSpawner']:
            self.system_profile_path = kwargs['config']['CustomSpawner']['system_profile_path']
        else:
            self.system_profile_path = os.path.dirname(self.config.JupyterHub.config_file) + "/jupyterhub/profiles"

        if 'user_profile_path' in kwargs['config']['CustomSpawner']:
            self.user_profile_path = kwargs['config']['CustomSpawner']['user_profile_path']
        else:
            self.user_profile_path = None

    def _options_form_default(self):
        self._load_profiles_from_fs()
        if len(self.profiles) > 0:
            temp_keys = [dict(display=p[0], key=p[1], type=p[2], id=p[1], first='') for p in self.profiles]
            temp_keys[0]['first'] = self.first_template
            text = ''.join([self.input_template.format(**tk) for tk in temp_keys])
            batch_command = self.profiles[0][4]
            batch_commands = dict()

            for profile in self.profiles:
                batch_commands[profile[1]] = profile[4]
            batch_commands = json.dumps(batch_commands)
            batch_commands = "var batch_commands = " + batch_commands
            select_handler = """function profile_selected(){
            var select = document.getElementById("profile_selector"); 
            var batch_command_id = select[select.selectedIndex].id;
            document.getElementById("batch_command").value = batch_commands[batch_command_id];
            }"""
            return self.form_template.format(input_template=text, batch_command=batch_command,
                                             batch_commands=batch_commands,
                                             select_handler=select_handler)
        else:
            return self.empty_form_template

    def options_from_form(self, formdata):
        # Default to first profile if somehow none is provided
        self._load_profiles_from_fs()
        self.config[self.spawner_class]['batch_script'] = str(formdata['batch_command'])
        return dict(profile=formdata.get('profile', [self.profiles[0][1]])[0])

    def _load_profiles_from_fs(self):
        '''This private function handles loading/re-loading custom profiles from
        the System profile directory and User profile directory listed in the
        configuration file.'''
        new_profiles = self._load_profiles(self.system_profile_path, "System:")
        self.profiles = new_profiles
        if self.user_profile_path is not None:
            path = self.user_profile_path + self.user.escaped_name + "/.jupyter/hub/profiles"
            new_profiles = self._load_profiles(path, "User:")
            for profile in new_profiles:
                self.profiles.append(profile)

        return

    def _load_profiles(self, filepath, prefix):
        '''This private function loads system profiles from the filepath specified,
        pre-pending each one with the prefix passed in'''
        new_profiles = []
        try:
            files = os.listdir(filepath)
            files = [filepath + "/" + f for f in files]
            count = 0
            for file in files:
                count += 1
                with open(file) as f:
                    lines = f.readlines()
                    f.seek(0)
                    option = prefix + lines[1]
                    option = option.replace("\n", "")
                    option = re.sub("#JUPYTER", "", option, re.IGNORECASE)
                    new_profiles.append(tuple((option,
                                               prefix + str(count), 'batchspawner.' + self.spawner_class,
                                               dict(req_nprocs='1', req_partition='compute', req_runtime='24:00:00'),
                                               f.read())))
        except Exception as e:
            pass
        return new_profiles

    def select_profile(self, profile):
        # Select matching profile, or do nothing (leaving previous or default config in place)
        for p in self.profiles:
            if p[1] == profile:
                self.child_class = p[2]
                self.child_config = p[3]
                break

    def construct_child(self):
        self.child_profile = self.user_options.get('profile', "")
        self.select_profile(self.child_profile)
        super().construct_child()

    def load_child_class(self, state):
        try:
            self.child_profile = state['profile']
        except KeyError:
            self.child_profile = ''
        self.select_profile(self.child_profile)

    def get_state(self):
        state = super().get_state()
        state['profile'] = self.child_profile
        return state

    def clear_state(self):
        super().clear_state()
        self.child_profile = ''
# vim: set ai expandtab softtabstop=4:
