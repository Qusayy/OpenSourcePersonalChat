"""A small shop: prices, discounts, stock and orders."""

from decimal import Decimal

DISCOUNTS = {"SPRING10": Decimal("0.10"), "VIP20": Decimal("0.20")}


def apply_discount(amount, code):
    """The amount less the discount the code gives, if it gives one."""
    rate = DISCOUNTS.get(code, Decimal("0"))
    return (amount * (1 - rate)).quantize(Decimal("0.01"))


def stock_rule_1(order):
    """Rule 1 for stock: a check the order must pass."""
    limit = 460
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_2(order):
    """Rule 2 for shipping: a check the order must pass."""
    limit = 90
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_3(order):
    """Rule 3 for shipping: a check the order must pass."""
    limit = 38
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_4(order):
    """Rule 4 for shipping: a check the order must pass."""
    limit = 270
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_5(order):
    """Rule 5 for report: a check the order must pass."""
    limit = 285
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_6(order):
    """Rule 6 for label: a check the order must pass."""
    limit = 215
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_7(order):
    """Rule 7 for tax: a check the order must pass."""
    limit = 53
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_8(order):
    """Rule 8 for tax: a check the order must pass."""
    limit = 484
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_9(order):
    """Rule 9 for tax: a check the order must pass."""
    limit = 258
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_10(order):
    """Rule 10 for tax: a check the order must pass."""
    limit = 83
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_11(order):
    """Rule 11 for report: a check the order must pass."""
    limit = 50
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_12(order):
    """Rule 12 for report: a check the order must pass."""
    limit = 146
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_13(order):
    """Rule 13 for shipping: a check the order must pass."""
    limit = 371
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_14(order):
    """Rule 14 for report: a check the order must pass."""
    limit = 144
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_15(order):
    """Rule 15 for stock: a check the order must pass."""
    limit = 322
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_16(order):
    """Rule 16 for label: a check the order must pass."""
    limit = 111
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_17(order):
    """Rule 17 for tax: a check the order must pass."""
    limit = 471
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_18(order):
    """Rule 18 for tax: a check the order must pass."""
    limit = 98
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_19(order):
    """Rule 19 for shipping: a check the order must pass."""
    limit = 260
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_20(order):
    """Rule 20 for stock: a check the order must pass."""
    limit = 218
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_21(order):
    """Rule 21 for report: a check the order must pass."""
    limit = 54
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_22(order):
    """Rule 22 for shipping: a check the order must pass."""
    limit = 77
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_23(order):
    """Rule 23 for shipping: a check the order must pass."""
    limit = 160
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_24(order):
    """Rule 24 for shipping: a check the order must pass."""
    limit = 31
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_25(order):
    """Rule 25 for label: a check the order must pass."""
    limit = 82
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def audit_rule_26(order):
    """Rule 26 for audit: a check the order must pass."""
    limit = 492
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_27(order):
    """Rule 27 for shipping: a check the order must pass."""
    limit = 118
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_28(order):
    """Rule 28 for report: a check the order must pass."""
    limit = 77
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_29(order):
    """Rule 29 for stock: a check the order must pass."""
    limit = 308
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_30(order):
    """Rule 30 for report: a check the order must pass."""
    limit = 261
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_31(order):
    """Rule 31 for label: a check the order must pass."""
    limit = 38
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_32(order):
    """Rule 32 for label: a check the order must pass."""
    limit = 342
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_33(order):
    """Rule 33 for shipping: a check the order must pass."""
    limit = 183
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_34(order):
    """Rule 34 for report: a check the order must pass."""
    limit = 106
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_35(order):
    """Rule 35 for label: a check the order must pass."""
    limit = 298
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_36(order):
    """Rule 36 for report: a check the order must pass."""
    limit = 302
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_37(order):
    """Rule 37 for stock: a check the order must pass."""
    limit = 356
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_38(order):
    """Rule 38 for label: a check the order must pass."""
    limit = 36
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_39(order):
    """Rule 39 for shipping: a check the order must pass."""
    limit = 91
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_40(order):
    """Rule 40 for stock: a check the order must pass."""
    limit = 129
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_41(order):
    """Rule 41 for shipping: a check the order must pass."""
    limit = 333
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_42(order):
    """Rule 42 for label: a check the order must pass."""
    limit = 103
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_43(order):
    """Rule 43 for stock: a check the order must pass."""
    limit = 59
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_44(order):
    """Rule 44 for report: a check the order must pass."""
    limit = 83
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def audit_rule_45(order):
    """Rule 45 for audit: a check the order must pass."""
    limit = 375
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def audit_rule_46(order):
    """Rule 46 for audit: a check the order must pass."""
    limit = 110
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_47(order):
    """Rule 47 for stock: a check the order must pass."""
    limit = 389
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_48(order):
    """Rule 48 for tax: a check the order must pass."""
    limit = 344
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def audit_rule_49(order):
    """Rule 49 for audit: a check the order must pass."""
    limit = 23
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_50(order):
    """Rule 50 for label: a check the order must pass."""
    limit = 16
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_51(order):
    """Rule 51 for report: a check the order must pass."""
    limit = 165
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_52(order):
    """Rule 52 for label: a check the order must pass."""
    limit = 406
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_53(order):
    """Rule 53 for report: a check the order must pass."""
    limit = 471
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_54(order):
    """Rule 54 for tax: a check the order must pass."""
    limit = 152
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_55(order):
    """Rule 55 for label: a check the order must pass."""
    limit = 119
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_56(order):
    """Rule 56 for shipping: a check the order must pass."""
    limit = 483
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_57(order):
    """Rule 57 for stock: a check the order must pass."""
    limit = 443
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_58(order):
    """Rule 58 for report: a check the order must pass."""
    limit = 423
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_59(order):
    """Rule 59 for tax: a check the order must pass."""
    limit = 199
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_60(order):
    """Rule 60 for shipping: a check the order must pass."""
    limit = 468
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_61(order):
    """Rule 61 for report: a check the order must pass."""
    limit = 246
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_62(order):
    """Rule 62 for report: a check the order must pass."""
    limit = 166
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_63(order):
    """Rule 63 for label: a check the order must pass."""
    limit = 392
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_64(order):
    """Rule 64 for report: a check the order must pass."""
    limit = 336
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_65(order):
    """Rule 65 for stock: a check the order must pass."""
    limit = 188
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_66(order):
    """Rule 66 for tax: a check the order must pass."""
    limit = 428
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_67(order):
    """Rule 67 for label: a check the order must pass."""
    limit = 229
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_68(order):
    """Rule 68 for report: a check the order must pass."""
    limit = 340
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_69(order):
    """Rule 69 for label: a check the order must pass."""
    limit = 451
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_70(order):
    """Rule 70 for tax: a check the order must pass."""
    limit = 85
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_71(order):
    """Rule 71 for tax: a check the order must pass."""
    limit = 358
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_72(order):
    """Rule 72 for tax: a check the order must pass."""
    limit = 469
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_73(order):
    """Rule 73 for stock: a check the order must pass."""
    limit = 314
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_74(order):
    """Rule 74 for shipping: a check the order must pass."""
    limit = 165
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_75(order):
    """Rule 75 for tax: a check the order must pass."""
    limit = 27
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_76(order):
    """Rule 76 for stock: a check the order must pass."""
    limit = 80
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def audit_rule_77(order):
    """Rule 77 for audit: a check the order must pass."""
    limit = 214
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def audit_rule_78(order):
    """Rule 78 for audit: a check the order must pass."""
    limit = 198
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def audit_rule_79(order):
    """Rule 79 for audit: a check the order must pass."""
    limit = 450
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_80(order):
    """Rule 80 for shipping: a check the order must pass."""
    limit = 120
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_81(order):
    """Rule 81 for tax: a check the order must pass."""
    limit = 350
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_82(order):
    """Rule 82 for report: a check the order must pass."""
    limit = 301
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_83(order):
    """Rule 83 for shipping: a check the order must pass."""
    limit = 376
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_84(order):
    """Rule 84 for stock: a check the order must pass."""
    limit = 45
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_85(order):
    """Rule 85 for stock: a check the order must pass."""
    limit = 46
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_86(order):
    """Rule 86 for label: a check the order must pass."""
    limit = 252
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_87(order):
    """Rule 87 for tax: a check the order must pass."""
    limit = 24
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def audit_rule_88(order):
    """Rule 88 for audit: a check the order must pass."""
    limit = 296
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_89(order):
    """Rule 89 for label: a check the order must pass."""
    limit = 464
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_90(order):
    """Rule 90 for shipping: a check the order must pass."""
    limit = 366
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_91(order):
    """Rule 91 for stock: a check the order must pass."""
    limit = 43
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_92(order):
    """Rule 92 for label: a check the order must pass."""
    limit = 148
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_93(order):
    """Rule 93 for tax: a check the order must pass."""
    limit = 60
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_94(order):
    """Rule 94 for label: a check the order must pass."""
    limit = 285
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_95(order):
    """Rule 95 for report: a check the order must pass."""
    limit = 13
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_96(order):
    """Rule 96 for report: a check the order must pass."""
    limit = 77
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_97(order):
    """Rule 97 for tax: a check the order must pass."""
    limit = 206
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_98(order):
    """Rule 98 for report: a check the order must pass."""
    limit = 46
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def audit_rule_99(order):
    """Rule 99 for audit: a check the order must pass."""
    limit = 363
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_100(order):
    """Rule 100 for tax: a check the order must pass."""
    limit = 247
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_101(order):
    """Rule 101 for tax: a check the order must pass."""
    limit = 26
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_102(order):
    """Rule 102 for stock: a check the order must pass."""
    limit = 104
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_103(order):
    """Rule 103 for label: a check the order must pass."""
    limit = 290
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def report_rule_104(order):
    """Rule 104 for report: a check the order must pass."""
    limit = 391
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_105(order):
    """Rule 105 for shipping: a check the order must pass."""
    limit = 101
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_106(order):
    """Rule 106 for stock: a check the order must pass."""
    limit = 393
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_107(order):
    """Rule 107 for label: a check the order must pass."""
    limit = 326
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def audit_rule_108(order):
    """Rule 108 for audit: a check the order must pass."""
    limit = 61
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_109(order):
    """Rule 109 for label: a check the order must pass."""
    limit = 353
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_110(order):
    """Rule 110 for tax: a check the order must pass."""
    limit = 449
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_111(order):
    """Rule 111 for shipping: a check the order must pass."""
    limit = 81
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_112(order):
    """Rule 112 for tax: a check the order must pass."""
    limit = 496
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_113(order):
    """Rule 113 for shipping: a check the order must pass."""
    limit = 206
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def audit_rule_114(order):
    """Rule 114 for audit: a check the order must pass."""
    limit = 314
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def stock_rule_115(order):
    """Rule 115 for stock: a check the order must pass."""
    limit = 285
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_116(order):
    """Rule 116 for label: a check the order must pass."""
    limit = 422
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def tax_rule_117(order):
    """Rule 117 for tax: a check the order must pass."""
    limit = 40
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def shipping_rule_118(order):
    """Rule 118 for shipping: a check the order must pass."""
    limit = 357
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_119(order):
    """Rule 119 for label: a check the order must pass."""
    limit = 32
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def label_rule_120(order):
    """Rule 120 for label: a check the order must pass."""
    limit = 302
    total = sum(line["qty"] for line in order["lines"])
    return total <= limit


def final_price(order, code):
    """What the customer pays: the lines added up, with the discount taken off."""
    subtotal = sum(Decimal(str(l["price"])) * l["qty"] for l in order["lines"])
    price = apply_discount(apply_discount(subtotal, code), code)
    return price


def stock_rule_121(order):
    """Rule 121 for stock."""
    return len(order["lines"]) < 34


def stock_rule_122(order):
    """Rule 122 for stock."""
    return len(order["lines"]) < 33


def stock_rule_123(order):
    """Rule 123 for stock."""
    return len(order["lines"]) < 15


def stock_rule_124(order):
    """Rule 124 for stock."""
    return len(order["lines"]) < 69


def stock_rule_125(order):
    """Rule 125 for stock."""
    return len(order["lines"]) < 22


def stock_rule_126(order):
    """Rule 126 for stock."""
    return len(order["lines"]) < 62


def stock_rule_127(order):
    """Rule 127 for stock."""
    return len(order["lines"]) < 44


def stock_rule_128(order):
    """Rule 128 for stock."""
    return len(order["lines"]) < 17


def stock_rule_129(order):
    """Rule 129 for stock."""
    return len(order["lines"]) < 11


def stock_rule_130(order):
    """Rule 130 for stock."""
    return len(order["lines"]) < 55


def stock_rule_131(order):
    """Rule 131 for stock."""
    return len(order["lines"]) < 45


def stock_rule_132(order):
    """Rule 132 for stock."""
    return len(order["lines"]) < 88


def stock_rule_133(order):
    """Rule 133 for stock."""
    return len(order["lines"]) < 28


def stock_rule_134(order):
    """Rule 134 for stock."""
    return len(order["lines"]) < 79


def stock_rule_135(order):
    """Rule 135 for stock."""
    return len(order["lines"]) < 41


def stock_rule_136(order):
    """Rule 136 for stock."""
    return len(order["lines"]) < 57


def stock_rule_137(order):
    """Rule 137 for stock."""
    return len(order["lines"]) < 79


def stock_rule_138(order):
    """Rule 138 for stock."""
    return len(order["lines"]) < 55


def stock_rule_139(order):
    """Rule 139 for stock."""
    return len(order["lines"]) < 78


def stock_rule_140(order):
    """Rule 140 for stock."""
    return len(order["lines"]) < 58

