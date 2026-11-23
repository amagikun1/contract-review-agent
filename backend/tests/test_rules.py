from backend.app.rules import check_rules, identify_clauses

def test_payment_after_acceptance_not_flagged_as_prepayment():
    text='采购合同。甲方在验收合格后支付全部款项。违约赔偿由双方承担。争议提交人民法院。'
    assert not any('RULE-PAY' in r['rule_ids'] for r in check_rules(text))

def test_identify_parenthesized_parties():
    assert identify_clauses('服务合同\n甲方（委托方）：青禾科技\n乙方（服务方）：远山科技')['parties']==['甲方（委托方）：青禾科技','乙方（服务方）：远山科技']
