#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""Simple utility PTransforms.
"""

from __future__ import absolute_import

from apache_beam.transforms.core import CombinePerKey
from apache_beam.transforms.core import Flatten
from apache_beam.transforms.core import GroupByKey
from apache_beam.transforms.core import Map
from apache_beam.transforms.ptransform import PTransform
from apache_beam.transforms.ptransform import ptransform_fn

__all__ = [
    'CoGroupByKey',
    'Keys',
    'KvSwap',
    'RemoveDuplicates',
    'Values',
    ]


class CoGroupByKey(PTransform):
  """Groups results across several PCollections by key.

  Given an input dict mapping serializable keys (called "tags") to 0 or more
  PCollections of (key, value) tuples, e.g.::

     {'pc1': pcoll1, 'pc2': pcoll2, 33333: pcoll3}

  creates a single output PCollection of (key, value) tuples whose keys are the
  unique input keys from all inputs, and whose values are dicts mapping each
  tag to an iterable of whatever values were under the key in the corresponding
  PCollection::

    ('some key', {'pc1': ['value 1 under "some key" in pcoll1',
                          'value 2 under "some key" in pcoll1'],
                  'pc2': [],
                  33333: ['only value under "some key" in pcoll3']})

  Note that pcoll2 had no values associated with "some key".

  CoGroupByKey also works for tuples, lists, or other flat iterables of
  PCollections, in which case the values of the resulting PCollections
  will be tuples whose nth value is the list of values from the nth
  PCollection---conceptually, the "tags" are the indices into the input.
  Thus, for this input::

     (pcoll1, pcoll2, pcoll3)

  the output PCollection's value for "some key" is::

    ('some key', (['value 1 under "some key" in pcoll1',
                   'value 2 under "some key" in pcoll1'],
                  [],
                  ['only value under "some key" in pcoll3']))

  Args:
    label: name of this transform instance. Useful while monitoring and
      debugging a pipeline execution.
    **kwargs: Accepts a single named argument "pipeline", which specifies the
      pipeline that "owns" this PTransform. Ordinarily CoGroupByKey can obtain
      this information from one of the input PCollections, but if there are none
      (or if there's a chance there may be none), this argument is the only way
      to provide pipeline information, and should be considered mandatory.
  """

  def __init__(self, **kwargs):
    super(CoGroupByKey, self).__init__()
    self.pipeline = kwargs.pop('pipeline', None)
    if kwargs:
      raise ValueError('Unexpected keyword arguments: %s' % kwargs.keys())

  def _extract_input_pvalues(self, pvalueish):
    try:
      # If this works, it's a dict.
      return pvalueish, tuple(pvalueish.viewvalues())
    except AttributeError:
      pcolls = tuple(pvalueish)
      return pcolls, pcolls

  def expand(self, pcolls):
    """Performs CoGroupByKey on argument pcolls; see class docstring."""
    # For associating values in K-V pairs with the PCollections they came from.
    def _pair_tag_with_value(key_value, tag):
      (key, value) = key_value
      return (key, (tag, value))

    # Creates the key, value pairs for the output PCollection. Values are either
    # lists or dicts (per the class docstring), initialized by the result of
    # result_ctor(result_ctor_arg).
    def _merge_tagged_vals_under_key(key_grouped, result_ctor,
                                     result_ctor_arg):
      (key, grouped) = key_grouped
      result_value = result_ctor(result_ctor_arg)
      for tag, value in grouped:
        result_value[tag].append(value)
      return (key, result_value)

    try:
      # If pcolls is a dict, we turn it into (tag, pcoll) pairs for use in the
      # general-purpose code below. The result value constructor creates dicts
      # whose keys are the tags.
      result_ctor_arg = pcolls.keys()
      result_ctor = lambda tags: dict((tag, []) for tag in tags)
      pcolls = pcolls.items()
    except AttributeError:
      # Otherwise, pcolls is a list/tuple, so we turn it into (index, pcoll)
      # pairs. The result value constructor makes tuples with len(pcolls) slots.
      pcolls = list(enumerate(pcolls))
      result_ctor_arg = len(pcolls)
      result_ctor = lambda size: tuple([] for _ in xrange(size))

    # Check input PCollections for PCollection-ness, and that they all belong
    # to the same pipeline.
    for _, pcoll in pcolls:
      self._check_pcollection(pcoll)
      if self.pipeline:
        assert pcoll.pipeline == self.pipeline

    return ([pcoll | 'pair_with_%s' % tag >> Map(_pair_tag_with_value, tag)
             for tag, pcoll in pcolls]
            | Flatten(pipeline=self.pipeline)
            | GroupByKey()
            | Map(_merge_tagged_vals_under_key, result_ctor, result_ctor_arg))


def Keys(label='Keys'):  # pylint: disable=invalid-name
  """Produces a PCollection of first elements of 2-tuples in a PCollection."""
  return label >> Map(lambda k_v: k_v[0])


def Values(label='Values'):  # pylint: disable=invalid-name
  """Produces a PCollection of second elements of 2-tuples in a PCollection."""
  return label >> Map(lambda k_v1: k_v1[1])


def KvSwap(label='KvSwap'):  # pylint: disable=invalid-name
  """Produces a PCollection reversing 2-tuples in a PCollection."""
  return label >> Map(lambda k_v2: (k_v2[1], k_v2[0]))


@ptransform_fn
def RemoveDuplicates(pcoll):  # pylint: disable=invalid-name
  """Produces a PCollection containing the unique elements of a PCollection."""
  return (pcoll
          | 'ToPairs' >> Map(lambda v: (v, None))
          | 'Group' >> CombinePerKey(lambda vs: None)
          | 'RemoveDuplicates' >> Keys())
