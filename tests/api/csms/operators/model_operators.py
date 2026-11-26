from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, EmailStr, StrictBool

VatType = Literal[
    "VAT_NONE",
    "VAT_0",
    "VAT_10",
    "VAT_20",
    "VAT_10_110",
    "VAT_20_120",
    "VAT_5",
    "VAT_7",
    "VAT_5_105",
    "VAT_7_107",
    "VAT_22",
    "VAT_22_122",
]


class OperatorCreateRequest(BaseModel):
    name: str
    yoomoneyMerchant: str
    sberbankMerchantLogin: str
    vatType: VatType
    companyInn: str
    companyEmail: str


class ManagerShortInfo(BaseModel):
    id: UUID
    keycloakId: UUID | None = None
    firstName: str | None = None
    lastName: str | None = None
    middleName: str | None = None
    phone: str | None = None
    email: EmailStr | None = None


class OperatorItem(OperatorCreateRequest):
    id: UUID
    createdBy: ManagerShortInfo | None = None
    updatedBy: ManagerShortInfo | None = None
    createdAt: AwareDatetime | None = None
    updatedAt: AwareDatetime | None = None
    deleted: StrictBool
    deletedAt: AwareDatetime | None = None


class ProblemDetail(BaseModel):
    type: str
    title: str
    status: int
    code: str
