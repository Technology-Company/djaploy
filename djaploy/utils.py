"""
Utility classes for djaploy
"""


class StringLike:
    """
    Base class for string-like objects that can be used in place of strings
    """
    
    def __init__(self, value=None):
        if value is not None:
            self._data = value

    def __str__(self):
        return self.data

    def __repr__(self):
        return f'{self.__class__.__name__}("{self.data}")'

    def __eq__(self, other):
        if isinstance(other, StringLike):
            return self.data == other.data
        return self.data == other

    def __hash__(self):
        return hash(self.data)

    def __len__(self):
        return len(self.data)

    def __getitem__(self, key):
        return self.data[key]

    def __contains__(self, item):
        return item in self.data

    def __iter__(self):
        return iter(self.data)

    def __add__(self, other):
        return self.data + str(other)

    def __radd__(self, other):
        return str(other) + self.data

    def __mod__(self, other):
        return self.data % other

    def format(self, *args, **kwargs):
        return self.data.format(*args, **kwargs)

    def join(self, iterable):
        return self.data.join(iterable)

    def split(self, sep=None, maxsplit=-1):
        return self.data.split(sep, maxsplit)

    def strip(self, chars=None):
        return self.data.strip(chars)

    def replace(self, old, new, count=-1):
        return self.data.replace(old, new, count)

    def startswith(self, prefix, start=None, end=None):
        return self.data.startswith(prefix, start, end)

    def endswith(self, suffix, start=None, end=None):
        return self.data.endswith(suffix, start, end)

    def lower(self):
        return self.data.lower()

    def upper(self):
        return self.data.upper()

    @property
    def data(self):
        """Override in subclasses to provide the actual string data"""
        return getattr(self, '_data', '')

    @data.setter
    def data(self, value):
        self._data = value