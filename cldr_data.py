"""CLDR 48 plural rule strings and raw sample lists.

Extracted from data/plurals.json + data/ordinals.json
(cldr-core@48.2.0, https://cldr.unicode.org) by tools/extract_cldr.py.
Do not edit by hand.
"""

CARDINAL_RULES = {'ar': {'few': 'n % 100 = 3..10',
        'many': 'n % 100 = 11..99',
        'one': 'n = 1',
        'other': None,
        'two': 'n = 2',
        'zero': 'n = 0'},
 'en': {'one': 'i = 1 and v = 0', 'other': None},
 'fr': {'many': 'e = 0 and i != 0 and i % 1000000 = 0 and v = 0 or e != 0..5', 'one': 'i = 0,1', 'other': None},
 'ja': {'other': None},
 'ru': {'few': 'v = 0 and i % 10 = 2..4 and i % 100 != 12..14',
        'many': 'v = 0 and i % 10 = 0 or v = 0 and i % 10 = 5..9 or v = 0 and i % 100 = 11..14',
        'one': 'v = 0 and i % 10 = 1 and i % 100 != 11',
        'other': None}}

CARDINAL_SAMPLE_TEXT = {'ar': {'few': 'integer 3~10, 103~110, 1003, … @decimal 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 103.0, 1003.0, …',
        'many': 'integer 11~26, 111, 1011, … @decimal 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 111.0, 1011.0, …',
        'one': 'integer 1 @decimal 1.0, 1.00, 1.000, 1.0000',
        'other': 'integer 100~102, 200~202, 300~302, 400~402, 500~502, 600, 1000, 10000, 100000, 1000000, … @decimal '
                 '0.1~0.9, 1.1~1.7, 10.1, 100.0, 1000.0, 10000.0, 100000.0, 1000000.0, …',
        'two': 'integer 2 @decimal 2.0, 2.00, 2.000, 2.0000',
        'zero': 'integer 0 @decimal 0.0, 0.00, 0.000, 0.0000'},
 'en': {'one': 'integer 1',
        'other': 'integer 0, 2~16, 100, 1000, 10000, 100000, 1000000, … @decimal 0.0~1.5, 10.0, 100.0, 1000.0, '
                 '10000.0, 100000.0, 1000000.0, …'},
 'fr': {'many': 'integer 1000000, 1c6, 2c6, 3c6, 4c6, 5c6, 6c6, … @decimal 1.0000001c6, 1.1c6, 2.0000001c6, 2.1c6, '
                '3.0000001c6, 3.1c6, …',
        'one': 'integer 0, 1 @decimal 0.0~1.5',
        'other': 'integer 2~17, 100, 1000, 10000, 100000, 1c3, 2c3, 3c3, 4c3, 5c3, 6c3, … @decimal 2.0~3.5, 10.0, '
                 '100.0, 1000.0, 10000.0, 100000.0, 1000000.0, 1.0001c3, 1.1c3, 2.0001c3, 2.1c3, 3.0001c3, 3.1c3, …'},
 'ja': {'other': 'integer 0~15, 100, 1000, 10000, 100000, 1000000, … @decimal 0.0~1.5, 10.0, 100.0, 1000.0, 10000.0, '
                 '100000.0, 1000000.0, …'},
 'ru': {'few': 'integer 2~4, 22~24, 32~34, 42~44, 52~54, 62, 102, 1002, …',
        'many': 'integer 0, 5~19, 100, 1000, 10000, 100000, 1000000, …',
        'one': 'integer 1, 21, 31, 41, 51, 61, 71, 81, 101, 1001, …',
        'other': 'decimal 0.0~1.5, 10.0, 100.0, 1000.0, 10000.0, 100000.0, 1000000.0, …'}}

ORDINAL_RULES = {'ar': {'other': None},
 'en': {'few': 'n % 10 = 3 and n % 100 != 13',
        'one': 'n % 10 = 1 and n % 100 != 11',
        'other': None,
        'two': 'n % 10 = 2 and n % 100 != 12'},
 'fr': {'one': 'n = 1', 'other': None},
 'ja': {'other': None},
 'ru': {'other': None}}

ORDINAL_SAMPLE_TEXT = {'ar': {'other': 'integer 0~15, 100, 1000, 10000, 100000, 1000000, …'},
 'en': {'few': 'integer 3, 23, 33, 43, 53, 63, 73, 83, 103, 1003, …',
        'one': 'integer 1, 21, 31, 41, 51, 61, 71, 81, 101, 1001, …',
        'other': 'integer 0, 4~18, 100, 1000, 10000, 100000, 1000000, …',
        'two': 'integer 2, 22, 32, 42, 52, 62, 72, 82, 102, 1002, …'},
 'fr': {'one': 'integer 1', 'other': 'integer 0, 2~16, 100, 1000, 10000, 100000, 1000000, …'},
 'ja': {'other': 'integer 0~15, 100, 1000, 10000, 100000, 1000000, …'},
 'ru': {'other': 'integer 0~15, 100, 1000, 10000, 100000, 1000000, …'}}
