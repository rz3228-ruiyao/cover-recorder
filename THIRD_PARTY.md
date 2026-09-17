# Third-party dependencies

This repository contains the Cover Recorder application source. Engine checkouts, compiled binaries and Python environments are fetched or built separately and are not vendored here.

| Dependency | Verified version / revision | Upstream licensing reference |
| --- | --- | --- |
| Tracktion Engine | 3.2.0 / `2877b621f2fbee564d0696a616b86bf8ba8c8ab0` | [License at the pinned revision](https://github.com/Tracktion/tracktion_engine/blob/2877b621f2fbee564d0696a616b86bf8ba8c8ab0/LICENSE.md) |
| JUCE | 8.0.12 / `7c89e11f6b7316c369f3d3f22227c60e816e738b` | [License at the pinned revision](https://github.com/juce-framework/JUCE/blob/7c89e11f6b7316c369f3d3f22227c60e816e738b/LICENSE.md) |
| Python media / UI dependencies | `environment.yml` | Each installed package retains its own license and notices |

The native application includes Tracktion's example UI helpers from `examples/common` through its external checkout. The upstream Tracktion license offers GPLv3-or-later or commercial licensing; the JUCE license describes AGPLv3 or commercial licensing. Refer to those upstream files for their actual terms. Public availability of this application repository does not replace dependency licenses.

A separate license for the original application code has not been selected in this source publication. No binary distribution or third-party license grant is included.
