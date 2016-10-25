# wrapspawner for Jupyterhub

[![Build Status](https://travis-ci.org/jupyterhub/wrapspawner.svg?branch=master)](https://travis-ci.org/jupyterhub/wrapspawner)

This package includes WrapSpawner and ProfilesSpawner, which provide mechanisms for runtime configuration of spawners.  The inspiration for their development was to allow users to select from a range of pre-defined batch job profiles, but their operation is completely generic.

## Installation

1. from root directory of this repo (where setup.py is), run `pip install -e .`

   If you don't actually need an editable version, you can simply run 
      `pip install https://github.com/jupyterhub/wrapspawner`

2. add lines in `jupyterhub_config.py` for the spawner you intend to use, e.g.
   
   ```python
      c = get_config()
      c.JupyterHub.spawner_class = 'wrapspawner.ProfilesSpawner'
   ```
3. Depending on the spawner, additional configuration will likely be needed.

## Wrapper and Profile Spawners

### Overview

`WrapSpawner` provides a mechanism to wrap the interface of a Spawner such that
the Spawner class to use for single-user servers can be chosen dynamically.
Subclasses may modify the class or properties of the child Spawner at any point
before `start()` is called (e.g. from Authenticator `pre_spawn` hooks or options form 
processing) and that state will be preserved on restart. The start/stop/poll
methods are not real coroutines, but simply pass through the Futures returned
by the wrapped Spawner class.

`ProfilesSpawner` leverages the `Spawner` options form feature to allow user-driven
configuration of Spawner classes while permitting:
   * configuration of Spawner classes that don't natively implement `options_form`
   * administrator control of allowed configuration changes
   * runtime choice of which Spawner backend to launch

### Example

The following is based on the author's configuration using [batchspawner](https://github.com/jupyterhub/batchspawner)
showing how to give users access to multiple job configurations on the batch scheduled
clusters, as well as an option to run a local notebook directly on the jupyterhub server.

   ```python
   c.JupyterHub.spawner_class = 'wrapspawner.ProfilesSpawner'
   c.Spawner.http_timeout = 120
   #------------------------------------------------------------------------------
   # BatchSpawnerBase configuration
   #   Providing default values that we may omit in the profiles
   #------------------------------------------------------------------------------
   c.BatchSpawnerBase.req_host = 'mesabi.xyz.edu'
   c.BatchSpawnerBase.req_runtime = '12:00:00'
   c.TorqueSpawner.state_exechost_exp = r'in-\1.mesabi.xyz.edu'
   #------------------------------------------------------------------------------
   # ProfilesSpawner configuration
   #------------------------------------------------------------------------------
   # List of profiles to offer for selection. Signature is:
   #   List(Tuple( Unicode, Unicode, Type(Spawner), Dict ))
   # corresponding to profile display name, unique key, Spawner class,
   # dictionary of spawner config options.
   # 
   # The first three values will be exposed in the input_template as {display},
   # {key}, and {type}
   #
   c.ProfilesSpawner.profiles = [
      ( "Local server", 'local', 'jupyterhub.spawner.LocalProcessSpawner', {'ip':'0.0.0.0'} ),
      ('Mesabi - 2 cores, 4 GB, 8 hours', 'mesabi2c4g12h', 'batchspawner.TorqueSpawner',
         dict(req_nprocs='2', req_queue='mesabi', req_runtime='8:00:00', req_memory='4gb')),
      ('Mesabi - 12 cores, 128 GB, 4 hours', 'mesabi128gb', 'batchspawner.TorqueSpawner',
         dict(req_nprocs='12', req_queue='ram256g', req_runtime='4:00:00', req_memory='125gb')),
      ('Mesabi - 2 cores, 4 GB, 24 hours', 'mesabi2c4gb24h', 'batchspawner.TorqueSpawner',
         dict(req_nprocs='2', req_queue='mesabi', req_runtime='24:00:00', req_memory='4gb')),
      ('Interactive Cluster - 2 cores, 4 GB, 8 hours', 'lab', 'batchspawner.TorqueSpawner',
         dict(req_nprocs='2', req_host='labhost.xyz.edu', req_queue='lab',
             req_runtime='8:00:00', req_memory='4gb', state_exechost_exp='')),
      ]
   ```
