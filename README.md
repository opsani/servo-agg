# servo-agg
Driver aggregator

The aggregator can be used to combine several drivers compatible with the Optune `servo` agent and present them all as a single driver.

Only `adjust` drivers are supported in this version.

## Setup

To use the aggregator:

- place the executable and the Python libraries into a single directory, e.g., `servo`
- in the same directory, create a sub-directory `adjust.d` and place all drivers that are to be aggregated into it. If those drivers need supporting files (Python modules, data files, etc.), also place them in `adjust.d`.
- the configuration file should be placed in the root directory where the aggregator is (not in `adjust.d`). This root directory should be the current directory when the aggregator is run (this is also where one would place the `servo` agent, just as one would when configuring it with a single adjust driver).

The resulting directory structure should look like this:

/servo/             # <= this will be the current directory when ‘adjust’ is ran
      /config.yaml  # configuration for the drivers
      /adjust       # the aggregator executable
      /adjust.py    # common library for drivers
      /util.py      # common library
      /servo        # optune servo agent
      /adjust.d/    # drivers directory (see NOTE)
               /driver1  # executable
               /driver2  # executable
               /datafile # not executable
               /...

NOTE:the only files in `adjust.d` that have the executable flag should be one or more `adjust` drivers. If the driver requires any supporting executables of its own, they should be installed elsewhere (OK to place them in a sub-directory of `adjust.d`)

After copying all files, the setup can be tested by running:

	./adjust --version

This should display a version string that includes the aggregator's own version followed by the versions of all drivers in `adjust.d` in parentheses.

## Operation

The aggregator behaves like a normal `adjust` driver and will be recognized as such by the `servo` agent.

When performing adjust, the drivers are ran in sequence (ordered by file name).

Progress is reported on the assumption that all drivers take the same time to run: each driver's progress advance is divided by the number of drivers to compute the overall advance (e.g., with 3 drivers, a driver that advances by 30% will be seen as 10% overall advance of the aggregator). Progress advance will be reported also after each driver's completion even if that driver did not send any interim progress messages (i.e., at the very least, there will be as many progress messages sent as there are drivers).


