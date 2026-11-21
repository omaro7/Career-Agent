# Customer PPTX template

Copy the original client file `고객사_프로필_한정훈_샘플.pptx` to this directory
with the filename `customer-profile.pptx`.

The PPTX contains personal information and embedded assets and is intentionally
not bundled in this source code archive. Do not check it into a public Git repo.

The Docker Worker mounts the full directory read-only at `/app/templates`.
The service never re-opens `input/profile.xlsx`: source career records come
from MySQL through Spring Boot only.
