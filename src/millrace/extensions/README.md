# Extensions

Extensions are declared additions to the generic runtime boundary.

An extension may provide domain behavior only through an explicit manifest and
contract selected by the workflow. It cannot bypass compiled authority,
capability or approval rules, or the kernel state-change boundary.

The v0.22 release keeps this surface deliberately small. New plugin,
provider, marketplace, and effect systems should be added only when a concrete
workflow requires them and their authority can be compiled and enforced.
