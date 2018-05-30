# nemo

Map filtering and SZ cluster detection and characterization pipeline. *Not* the pipeline used
for [Hasselfield et al. (2013)](http://adsabs.harvard.edu/abs/2013JCAP...07..008H), but should 
give similar results for signal-to-noise, given the same map (or at least it did in the past).
*It is* the pipeline, that has been used for the two-season ACTPol cluster catalog 
[paper](http://adsabs.harvard.edu/abs/2017arXiv170905600H).
See `examples/equD56/README.md` if you would like to re-create the cluster catalog produced for the
E-D56 field, and `examples/AdvACT/` if you want to see example .par files currently being used 
for the AdvACT cluster search.

This code was originally posted "as is", in time for the Feb 2017 meeting, and it is still in need 
of a tidy up. There are several different types of filters and options to play with, but the only
one which is well-tested and recommended at the moment is the RealSpaceMatchedFilter. 
Again, see `examples/AdvACT` (multi-frequency) and `examples/equD56` (single frequency) for 
examples of its use.

Note that the catalog matching and (static) web page generation code that was in `nemo` has been
removed, as that's all now handled by the [sourcery](https://github.com/mattyowl/sourcery) package, 
which runs a live, editable web database.

Catalogs produced by nemo can also be fed into the [zCluster](https://github.com/ACTCollaboration/zCluster)
code, for estimating cluster photometric redshifts.

Matthew Hasselfield's `gnfw.py` code (used for Arnaud profiles) is included in nemo, as is a modified 
version of `actDict.py` - thanks to whoever wrote that back in the day.

## Software needed

Nemo itself is written in python (2.7.x), and requires the following additional modules to be installed 
(current versions used by the author are given in brackets, earlier and later versions also probably work):

* numpy (1.11.1)
* scipy (0.17.1)
* matplotlib (2.1.0)
* astLib (0.10.x + or git version: get it with `git clone http://git.code.sf.net/p/astlib/git astlib-git`)
* flipper ([ACT collaboration git version](https://github.com/ACTCollaboration/flipper))
* Python Imaging Library (PIL or PILLOW - e.g., `sudo easy_install Pillow` on Mac)
* astropy (2.0.4)
* IPython (2.4.1)
* Cython (0.24.1)
* colorcet (1.0.0; https://github.com/bokeh/colorcet/releases)

_Note:_ Switched from `atpy` and `pyfits` to `astropy` for handling .fits tables and images. `enlib` is 
currently being used only for CMB-only sky sims (for estimating false positive detection rate), but
other parts which currently rely on `flipper` may be replaced in future.

Most of the dependencies can be installed using `pip`.

## Installation

As root:
    
```
sudo python setup.py install
```

Or, in your home directory:
    
```
python setup.py install --prefix=$HOME/local
```

Then add `$HOME/local/bin` to $PATH, and e.g., `$HOME/local/lib/python2.7/site-packages` to $PYTHONPATH.

```
export PATH=$HOME/local/bin:$PATH    
export PYTHONPATH=$HOME/local/lib/python2.7/site-packages:$PYTHONPATH
```

## Running nemo

See `examples/equD56` for a tutorial on how to re-create the ACTPol two-season cluster catalog (including mass estimates).
See `examples/AdvACT` for example .par files for the current AdvACT cluster search. Refer to the comments in the .par
files themselves for information on what each parameter does.

## Comments, bug reports, help, suggestions etc..

Please contact Matt Hilton <matt.hilton@mykolab.com>.
