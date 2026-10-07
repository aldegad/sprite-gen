# SPDX-License-Identifier: Apache-2.0
"""The shared acceptance policy for RIFE frames in repair and cycle alignment.

Measurements live in ``rife.smear``; these are the existing alignment bounds,
also applied before replacing a filmed middle frame during jump repair.
"""

SMEAR_WARN = 0.001
OUTLINE_WARN = 0.05


def faults(measure: dict[str, float]) -> list[str]:
    """Smear inside the body or outline loss beyond both neighbours."""
    return ([*(["smear"] if measure["dark_excess"] > SMEAR_WARN else []),
             *(["outline"] if measure["outline_loss"] > OUTLINE_WARN else [])])
