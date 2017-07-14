# wrapspawner for Jupyterhub

[![Build Status](https://travis-ci.org/jupyterhub/wrapspawner.svg?branch=master)](https://travis-ci.org/jupyterhub/wrapspawner)

This package includes WrapSpawner and ProfilesSpawner, which provide mechanisms for runtime configuration of spawners.  The inspiration for their development was to allow users to select from a range of pre-defined batch job profiles, but their operation is completely generic.

## Installation

1. from root directory of this repo (where setup.py is), run `pip install -e .`

   If you don't actually need an editable version, you can simply run 
      `pip install git+https://github.com/jupyterhub/wrapspawner`

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

The following is used to provide to the user a way to choose from a dropdown menu either to run a Jupyter Notebook
as a local process in the local server or choose 2 different Docker Images to run within `DockerSpawner`.

   ```python
   c.JupyterHub.spawner_class = 'wrapspawner.ProfilesSpawner'
   c.Spawner.http_timeout = 120
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
          ( "Host process", 'local', 'jupyterhub.spawner.LocalProcessSpawner', {'ip':'0.0.0.0'} ),
          ('Docker Python 2/3', 'systemuser', 'dockerspawner.SystemUserSpawner', dict(container_image="jupyterhub/systemuser")),
          ('Docker Python 2/3,R,Julia', 'datasciencesystemuser', 'dockerspawner.SystemUserSpawner', dict(container_image="jupyterhub/datasciencesystemuser")),
    ]
   ```

These mechanisms originated as part of the [`batchspawner`](https://github.com/jupyterhub/batchspawner) package. The `batchspawner` README contains additional examples on the use of ProfilesSpawner.
