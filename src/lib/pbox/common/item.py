# -*- coding: UTF-8 -*-
import inspect
import re
import yaml
from tinyscript import functools, logging
from tinyscript.helpers import set_exception, Path
from tinyscript.report import *

from .config import config


__all__ = ["update_logger", "Item"]


_fmt_name = lambda x: (x or "").lower().replace("_", "-")

set_exception("NotInstantiable", "TypeError")


def update_logger(m):
    """ Method decorator for triggering the setting of the bound logger (see pbox.common.Item.__getattribute__). """
    @functools.wraps(m)
    def _wrapper(self, *a, **kw):
        getattr(self, "logger", None)
        return m(self, *a, **kw)
    return _wrapper


class MetaItem(type):
    def __getattribute__(self, name):
        # this masks some attributes for child classes (e.g. Algorithm.registry can be accessed, but when the registry
        #  of child classes is computed, the child classes, e.g. RF, won't be able to access RF.registry)
        if name in ["get", "iteritems", "mro", "registry"] and self._instantiable:
            raise AttributeError(name)
        return super(MetaItem, self).__getattribute__(name)
    
    @property
    def source(self):
        return self._source
    
    @source.setter
    def source(self, path):
        # case 1: self is a parent class among Analyzer, Detector, ... ;
        #          then 'source' means the source path for loading child classes
        try:
            p = Path(str(path or config['%ss' % self.__name__.lower()]))
            if hasattr(self, "_source") and self._source == p:
                return
            self._source = p
        # case 2: self is a child class of Analyzer, Detector, ... ;
        #          then 'source' is an attribute that comes from the YAML definition
        except KeyError:
            return
        # now make the registry from the given source path
        def _setattr(i, d):
            for k, v in d.items():
                setattr(i, "_" + k if k in ["source", "status"] else k, v)
        # open the .conf file associated to the main class (i.e. Detector, Packer, ...)
        self.registry, glob = [], inspect.getparentframe().f_back.f_globals
        with p.open() as f:
            items = yaml.load(f, Loader=yaml.Loader)
        # start parsing items of the target class
        _cache, defaults = {}, items.pop('defaults', {})
        for item, data in items.items():
            for k, v in defaults.items():
                if k in ["base", "install", "status", "steps", "variants"]:
                    raise ValueError("parameter '%s' cannot have a default value" % k)
                data.setdefault(k, v)
            # ensure the related item is available in module's globals()
            #  NB: the item may already be in globals in some cases like pbox.items.packer.Ezuri
            if item not in glob:
                d = dict(self.__dict__)
                del d['registry']
                glob[item] = type(item, (self, ), d)
            i = glob[item]
            i._instantiable = True
            # before setting attributes from the YAML parameters, check for 'base' ; this allows to copy all attributes
            #  from an entry originating from another item class (i.e. copying from Packer's equivalent to Unpacker ;
            #  e.g. UPX)
            base = data.get('base')  # i.e. detector|packer|unpacker ; DO NOT pop as 'base' is also used for algorithms
            if isinstance(base, str):
                m = re.match(r"(?i)(detector|packer|unpacker)(?:\[(.*?)\])?$", str(base))
                if m:
                    data.pop('base')
                    base, bcls = m.groups()
                    base, bcls = base.capitalize(), bcls or item
                    if base == self.__name__ and bcls in [None, item]:
                        raise ValueError("%s cannot point to itself" % item)
                    if base not in _cache.keys():
                        with Path(config[base.lower() + "s"]).open() as f:
                            _cache[base] = yaml.load(f, Loader=yaml.Loader)
                    for k, v in _cache[base].get(bcls, {}).items():
                        # do not process these keys as they shall be different from an item class to another anyway
                        if k in ["steps", "status"]:
                            continue
                        setattr(i, "_" + k if k == "source" else k, v)
                else:
                    raise ValueError("'base' set to '%s' of %s discarded (bad format)" % (base, item))
            # check for variants ; the goal is to copy the current item class and to adapt the fields from the variants
            #  to the new classes (note that on the contrary of base, a variant inherits the 'status' parameter)
            variants, vilist = data.pop('variants', {}), []
            for vitem in variants.keys():
                d = dict(self.__dict__)
                del d['registry']
                vi = glob[vitem] = type(vitem, (self, ), d)
                vi._instantiable = True
                vi.parent = item
                vilist.append(vi)
            # now set attributes from YAML parameters
            for it in [i] + vilist:
                _setattr(it, data)
            glob['__all__'].append(item)
            self.registry.append(i())
            # overwrite parameters specific to variants
            for vitem, vdata in variants.items():
                vi = glob[vitem]
                _setattr(vi, vdata)
                glob['__all__'].append(vitem)
                self.registry.append(vi())


class Item(metaclass=MetaItem):
    """ Item abstraction. """
    _instantiable = False
    
    def __init__(self, **kwargs):
        cls = self.__class__
        self.cname = cls.__name__
        self.name = _fmt_name(cls.__name__)
        self.type = cls.__base__.__name__.lower()
    
    def __new__(cls, *args, **kwargs):
        """ Prevents Item from being instantiated. """
        if cls._instantiable:
            return object.__new__(cls, *args, **kwargs)
        raise NotInstantiable("%s cannot be instantiated directly" % cls.__name__)
    
    def __repr__(self):
        """ Custom string representation for an item. """
        return "<%s %s at 0x%x>" % (self.__class__.__name__, self.type, id(self))
    
    def help(self):
        """ Returns a help message in Markdown format. """
        md = Report()
        if getattr(self, "description", None):
            md.append(Text(self.description))
        if getattr(self, "comment", None):
            md.append(Blockquote("**Note**: " + self.comment))
        if getattr(self, "link", None):
            md.append(Blockquote("**Link**: " + self.link))
        if getattr(self, "references", None):
            md.append(Section("References"), List(*self.references, **{'ordered': True}))
        return md.md()
    
    @classmethod
    def get(cls, item, error=True):
        """ Simple class method for returning the class of an item based on its name (case-insensitive). """
        for i in cls.registry:
            if i.name == (item.name if isinstance(item, Item) else _fmt_name(item)):
                return i
        if error:
            raise ValueError("'%s' is not defined" % item)
    
    @classmethod
    def iteritems(cls):
        """ Class-level iterator for returning enabled items. """
        for i in cls.registry:
            try:
                if i.status in i.__class__._enabled:
                    yield i
            except AttributeError:
                yield i
    
    @property
    def logger(self):
        if getattr(self, "_logger", None) is None:
            self._logger = logging.getLogger(self.name)
            logging.setLogger(self.name)
        return self._logger
    
    @property
    def source(self):
        return self.__class__._source

