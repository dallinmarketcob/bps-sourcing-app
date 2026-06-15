"""Readers: pull and normalize data from each external source.

Each reader returns engine-native models (Touch / Subscription) so the brain
never sees a vendor-specific shape. The CSV source-map loader lives here too.
"""
