-- PROPOSAL: review taxonomy before running. Execute on InventoryAI_Test first.
-- No product names, SKUs, categories, inventory, sales or BusinessRules are changed.
-- Change target only after separate authorization and successful test validation.
USE [InventoryAI_Test];
GO
SET XACT_ABORT ON;
BEGIN TRY
 BEGIN TRANSACTION;
 IF DB_NAME() <> N'InventoryAI_Test' THROW 51001, 'Unexpected database', 1;
 DECLARE @Map TABLE (product_id int PRIMARY KEY, sku nvarchar(20), product_name nvarchar(300), category nvarchar(100), subcategory_name nvarchar(200));
 INSERT INTO @Map(product_id,sku,product_name,category,subcategory_name) VALUES
(1,N'SKU-0001',N'iPhone 16',N'Smartphones',N'Standard Smartphone'),
(2,N'SKU-0002',N'MacBook Air M3',N'Laptops',N'Thin-and-light Laptop'),
(3,N'SKU-0003',N'iPad Air',N'Tablets',N'Midrange Tablet'),
(4,N'SKU-0004',N'AirPods Pro 2',N'Audio',N'Earbuds'),
(5,N'SKU-0005',N'Galaxy S25',N'Smartphones',N'Premium Smartphone'),
(6,N'SKU-0006',N'Neo QLED 4K QN90D',N'Televisions',N'QLED/QNED TV'),
(7,N'SKU-0007',N'Galaxy Tab S10',N'Tablets',N'Performance Tablet'),
(8,N'SKU-0008',N'Galaxy Buds3 Pro',N'Audio',N'Earbuds'),
(9,N'SKU-0009',N'OLED evo C4 4K TV',N'Televisions',N'OLED TV'),
(10,N'SKU-0010',N'XBOOM Go Speaker',N'Audio',N'Speaker'),
(11,N'SKU-0011',N'DualCool Inverter AC',N'Appliances',N'Air conditioner'),
(12,N'SKU-0012',N'BRAVIA 8 OLED 4K',N'Televisions',N'OLED TV'),
(13,N'SKU-0013',N'WH-1000XM5',N'Audio',N'Headphones'),
(14,N'SKU-0014',N'Alpha 7 IV',N'Cameras',N'Mirrorless Camera'),
(15,N'SKU-0015',N'XPS 13',N'Laptops',N'Thin-and-light Laptop'),
(16,N'SKU-0016',N'Spectre x360 14',N'Laptops',N'Thin-and-light Laptop'),
(17,N'SKU-0017',N'ThinkPad E14 Gen 6',N'Laptops',N'Business Laptop'),
(18,N'SKU-0018',N'Tab P12',N'Tablets',N'Midrange Tablet'),
(19,N'SKU-0019',N'Zenbook 14 OLED',N'Laptops',N'Thin-and-light Laptop'),
(20,N'SKU-0020',N'OnePlus 13',N'Smartphones',N'Premium Smartphone'),
(21,N'SKU-0021',N'OnePlus Buds Pro 3',N'Audio',N'Earbuds'),
(22,N'SKU-0022',N'Xiaomi 14',N'Smartphones',N'Premium Smartphone'),
(23,N'SKU-0023',N'Xiaomi XIA-TEL-1',N'Televisions',N'General Television'),
(24,N'SKU-0024',N'Airdopes 311 Pro',N'Audio',N'Earbuds'),
(25,N'SKU-0025',N'EOS R6 Mark II',N'Cameras',N'Mirrorless Camera'),
(26,N'SKU-0026',N'iPhone 16 Plus',N'Smartphones',N'Standard Smartphone'),
(27,N'SKU-0027',N'MacBook Pro 14 M3',N'Laptops',N'General Laptop'),
(28,N'SKU-0028',N'iPad Pro 11',N'Tablets',N'Performance Tablet'),
(29,N'SKU-0029',N'AirPods 4',N'Audio',N'Earbuds'),
(30,N'SKU-0030',N'Galaxy S25 Ultra',N'Smartphones',N'Premium Smartphone'),
(31,N'SKU-0031',N'OLED S90D 4K TV',N'Televisions',N'OLED TV'),
(32,N'SKU-0032',N'Galaxy Tab S10 Ultra',N'Tablets',N'Performance Tablet'),
(33,N'SKU-0033',N'Galaxy Buds3',N'Audio',N'Earbuds'),
(34,N'SKU-0034',N'OLED B4 4K TV',N'Televisions',N'OLED TV'),
(35,N'SKU-0035',N'XBOOM XL7',N'Audio',N'Speaker'),
(36,N'SKU-0036',N'Smart Refrigerator',N'Appliances',N'Refrigerator'),
(37,N'SKU-0037',N'BRAVIA 7 Mini LED 4K',N'Televisions',N'Mini LED TV'),
(38,N'SKU-0038',N'WF-1000XM5',N'Audio',N'Earbuds'),
(39,N'SKU-0039',N'Alpha 6700',N'Cameras',N'Mirrorless Camera'),
(40,N'SKU-0040',N'XPS 14',N'Laptops',N'Thin-and-light Laptop'),
(41,N'SKU-0041',N'Envy x360 14',N'Laptops',N'Thin-and-light Laptop'),
(42,N'SKU-0042',N'ThinkPad E16 Gen 2',N'Laptops',N'Business Laptop'),
(43,N'SKU-0043',N'Tab M11',N'Tablets',N'General Tablet'),
(44,N'SKU-0044',N'Vivobook S 14',N'Laptops',N'Thin-and-light Laptop'),
(45,N'SKU-0045',N'OnePlus 13R',N'Smartphones',N'Premium Smartphone'),
(46,N'SKU-0046',N'OnePlus Buds 3',N'Audio',N'Earbuds'),
(47,N'SKU-0047',N'Xiaomi 14 Ultra',N'Smartphones',N'Premium Smartphone'),
(48,N'SKU-0048',N'Xiaomi XIA-TEL-2',N'Televisions',N'General Television'),
(49,N'SKU-0049',N'Airdopes 141',N'Audio',N'Earbuds'),
(50,N'SKU-0050',N'EOS R8',N'Cameras',N'Mirrorless Camera'),
(51,N'SKU-0051',N'iPhone 16 Pro',N'Smartphones',N'Premium Smartphone'),
(52,N'SKU-0052',N'MacBook Pro 16 M3',N'Laptops',N'General Laptop'),
(53,N'SKU-0053',N'iPad Pro 13',N'Tablets',N'Performance Tablet'),
(54,N'SKU-0054',N'AirPods Max',N'Audio',N'Headphones'),
(55,N'SKU-0055',N'Galaxy A56',N'Smartphones',N'Standard Smartphone'),
(56,N'SKU-0056',N'Crystal UHD 4K DU8000',N'Televisions',N'Crystal UHD TV'),
(57,N'SKU-0057',N'Galaxy Tab S9 FE',N'Tablets',N'General Tablet'),
(58,N'SKU-0058',N'HW-Q990D Soundbar',N'Audio',N'Soundbar'),
(59,N'SKU-0059',N'QNED 4K TV',N'Televisions',N'QLED/QNED TV'),
(60,N'SKU-0060',N'Soundbar S90TR',N'Audio',N'Soundbar'),
(61,N'SKU-0061',N'Front Load Washer',N'Appliances',N'Washing machine'),
(62,N'SKU-0062',N'BRAVIA 3 4K',N'Televisions',N'General Television'),
(63,N'SKU-0063',N'ULT Field 7',N'Audio',N'Speaker'),
(64,N'SKU-0064',N'ZV-E10 II',N'Cameras',N'Mirrorless Camera'),
(65,N'SKU-0065',N'Inspiron 14',N'Laptops',N'General Laptop'),
(66,N'SKU-0066',N'Pavilion Plus 14',N'Laptops',N'General Laptop'),
(67,N'SKU-0067',N'ThinkPad T14 Gen 5',N'Laptops',N'Business Laptop'),
(68,N'SKU-0068',N'Tab Plus',N'Tablets',N'Midrange Tablet'),
(69,N'SKU-0069',N'ROG Zephyrus G14',N'Laptops',N'Gaming Laptop'),
(70,N'SKU-0070',N'OnePlus Nord 4',N'Smartphones',N'Standard Smartphone'),
(71,N'SKU-0071',N'OnePlus Nord Buds 3 Pro',N'Audio',N'Earbuds'),
(72,N'SKU-0072',N'Redmi Note 14 Pro',N'Smartphones',N'Standard Smartphone'),
(73,N'SKU-0073',N'Xiaomi XIA-TEL-3',N'Televisions',N'General Television'),
(74,N'SKU-0074',N'Rockerz 450',N'Audio',N'Headphones'),
(75,N'SKU-0075',N'EOS R50',N'Cameras',N'Mirrorless Camera'),
(76,N'SKU-0076',N'iPhone 16 Pro Max',N'Smartphones',N'Premium Smartphone'),
(77,N'SKU-0077',N'MacBook Air 15 M3',N'Laptops',N'Thin-and-light Laptop'),
(78,N'SKU-0078',N'iPad 10th Gen',N'Tablets',N'General Tablet'),
(79,N'SKU-0079',N'Beats Studio Buds+',N'Audio',N'Earbuds'),
(80,N'SKU-0080',N'Galaxy Z Flip6',N'Smartphones',N'Foldable Smartphone'),
(81,N'SKU-0081',N'Neo QLED 8K QN900D',N'Televisions',N'QLED/QNED TV'),
(82,N'SKU-0082',N'Galaxy Tab A9+',N'Tablets',N'General Tablet'),
(83,N'SKU-0083',N'Music Frame Speaker',N'Audio',N'Speaker'),
(84,N'SKU-0084',N'NanoCell 4K TV',N'Televisions',N'NanoCell TV'),
(85,N'SKU-0085',N'Soundbar S80QY',N'Audio',N'Soundbar'),
(86,N'SKU-0086',N'HeatPump Dryer',N'Appliances',N'Dryer'),
(87,N'SKU-0087',N'BRAVIA XR A95L',N'Televisions',N'OLED TV'),
(88,N'SKU-0088',N'HT-A7000 Soundbar',N'Audio',N'Soundbar'),
(89,N'SKU-0089',N'Cyber-shot RX100 VII',N'Cameras',N'Compact Camera'),
(90,N'SKU-0090',N'Inspiron 16',N'Laptops',N'General Laptop'),
(91,N'SKU-0091',N'EliteBook 840 G11',N'Laptops',N'Business Laptop'),
(92,N'SKU-0092',N'ThinkPad T16 Gen 3',N'Laptops',N'Business Laptop'),
(93,N'SKU-0093',N'Legion Tab',N'Tablets',N'Performance Tablet'),
(94,N'SKU-0094',N'ROG Strix G16',N'Laptops',N'Gaming Laptop'),
(95,N'SKU-0095',N'OnePlus Nord CE4',N'Smartphones',N'Standard Smartphone'),
(96,N'SKU-0096',N'OnePlus Buds Pro 3 - Variant 2',N'Audio',N'Earbuds'),
(97,N'SKU-0097',N'Redmi Note 14',N'Smartphones',N'Standard Smartphone'),
(98,N'SKU-0098',N'Xiaomi XIA-TEL-4',N'Televisions',N'General Television'),
(99,N'SKU-0099',N'Stone 1508',N'Audio',N'Speaker'),
(100,N'SKU-0100',N'PowerShot G7 X Mark III',N'Cameras',N'Compact Camera'),
(101,N'SKU-0101',N'iPhone 16 - Variant 2',N'Smartphones',N'Standard Smartphone'),
(102,N'SKU-0102',N'MacBook Air M3 - Variant 2',N'Laptops',N'Thin-and-light Laptop'),
(103,N'SKU-0103',N'iPad Air - Variant 2',N'Tablets',N'Midrange Tablet'),
(104,N'SKU-0104',N'AirPods Pro 2 - Variant 2',N'Audio',N'Earbuds'),
(105,N'SKU-0105',N'Galaxy S25 - Variant 2',N'Smartphones',N'Premium Smartphone'),
(106,N'SKU-0106',N'Neo QLED 4K QN90D - Variant 2',N'Televisions',N'QLED/QNED TV'),
(107,N'SKU-0107',N'Galaxy Tab S10 - Variant 2',N'Tablets',N'Performance Tablet'),
(108,N'SKU-0108',N'Galaxy Buds3 Pro - Variant 2',N'Audio',N'Earbuds'),
(109,N'SKU-0109',N'OLED evo C4 4K TV - Variant 2',N'Televisions',N'OLED TV'),
(110,N'SKU-0110',N'XBOOM Go Speaker - Variant 2',N'Audio',N'Speaker'),
(111,N'SKU-0111',N'DualCool Inverter AC - Variant 2',N'Appliances',N'Air conditioner'),
(112,N'SKU-0112',N'BRAVIA 8 OLED 4K - Variant 2',N'Televisions',N'OLED TV'),
(113,N'SKU-0113',N'WH-1000XM5 - Variant 2',N'Audio',N'Headphones'),
(114,N'SKU-0114',N'Alpha 7 IV - Variant 2',N'Cameras',N'Mirrorless Camera'),
(115,N'SKU-0115',N'Latitude 5450',N'Laptops',N'Business Laptop'),
(116,N'SKU-0116',N'ProBook 440 G11',N'Laptops',N'Business Laptop'),
(117,N'SKU-0117',N'IdeaPad Slim 5',N'Laptops',N'Thin-and-light Laptop'),
(118,N'SKU-0118',N'Tab P12 - Variant 2',N'Tablets',N'Midrange Tablet'),
(119,N'SKU-0119',N'TUF Gaming A15',N'Laptops',N'Gaming Laptop'),
(120,N'SKU-0120',N'OnePlus 13 - Variant 2',N'Smartphones',N'Premium Smartphone'),
(121,N'SKU-0121',N'OnePlus Buds 3 - Variant 2',N'Audio',N'Earbuds'),
(122,N'SKU-0122',N'Xiaomi 14 - Variant 2',N'Smartphones',N'Premium Smartphone'),
(123,N'SKU-0123',N'Xiaomi XIA-TEL-5',N'Televisions',N'General Television'),
(124,N'SKU-0124',N'Nirvana Ion',N'Audio',N'Earbuds'),
(125,N'SKU-0125',N'EOS R6 Mark II - Variant 2',N'Cameras',N'Mirrorless Camera'),
(126,N'SKU-0126',N'iPhone 16 Plus - Variant 2',N'Smartphones',N'Standard Smartphone'),
(127,N'SKU-0127',N'MacBook Pro 14 M3 - Variant 2',N'Laptops',N'General Laptop'),
(128,N'SKU-0128',N'iPad Pro 11 - Variant 2',N'Tablets',N'Performance Tablet'),
(129,N'SKU-0129',N'AirPods 4 - Variant 2',N'Audio',N'Earbuds'),
(130,N'SKU-0130',N'Galaxy S25 Ultra - Variant 2',N'Smartphones',N'Premium Smartphone'),
(131,N'SKU-0131',N'OLED S90D 4K TV - Variant 2',N'Televisions',N'OLED TV'),
(132,N'SKU-0132',N'Galaxy Tab S10 Ultra - Variant 2',N'Tablets',N'Performance Tablet'),
(133,N'SKU-0133',N'Galaxy Buds3 - Variant 2',N'Audio',N'Earbuds'),
(134,N'SKU-0134',N'OLED B4 4K TV - Variant 2',N'Televisions',N'OLED TV'),
(135,N'SKU-0135',N'XBOOM XL7 - Variant 2',N'Audio',N'Speaker'),
(136,N'SKU-0136',N'Smart Refrigerator - Variant 2',N'Appliances',N'Refrigerator'),
(137,N'SKU-0137',N'BRAVIA 7 Mini LED 4K - Variant 2',N'Televisions',N'Mini LED TV'),
(138,N'SKU-0138',N'WF-1000XM5 - Variant 2',N'Audio',N'Earbuds'),
(139,N'SKU-0139',N'Alpha 6700 - Variant 2',N'Cameras',N'Mirrorless Camera'),
(140,N'SKU-0140',N'Latitude 7450',N'Laptops',N'Business Laptop'),
(141,N'SKU-0141',N'Victus 16',N'Laptops',N'Gaming Laptop'),
(142,N'SKU-0142',N'IdeaPad Pro 5',N'Laptops',N'General Laptop'),
(143,N'SKU-0143',N'Tab M11 - Variant 2',N'Tablets',N'General Tablet'),
(144,N'SKU-0144',N'Zenbook 14 OLED - Variant 2',N'Laptops',N'Thin-and-light Laptop'),
(145,N'SKU-0145',N'OnePlus 13R - Variant 2',N'Smartphones',N'Premium Smartphone'),
(146,N'SKU-0146',N'OnePlus Nord Buds 3 Pro - Variant 2',N'Audio',N'Earbuds'),
(147,N'SKU-0147',N'Xiaomi 14 Ultra - Variant 2',N'Smartphones',N'Premium Smartphone'),
(148,N'SKU-0148',N'Xiaomi XIA-TEL-6',N'Televisions',N'General Television'),
(149,N'SKU-0149',N'Airdopes 311 Pro - Variant 2',N'Audio',N'Earbuds'),
(150,N'SKU-0150',N'EOS R8 - Variant 2',N'Cameras',N'Mirrorless Camera');
 IF (SELECT COUNT(*) FROM @Map) <> 150 OR (SELECT COUNT(*) FROM dbo.Products) <> 150
   THROW 51002, 'Expected 150 products and 150 mapping rows', 1;
 IF EXISTS (SELECT 1 FROM @Map m LEFT JOIN dbo.Products p WITH (UPDLOCK,HOLDLOCK) ON p.product_id=m.product_id AND p.sku=m.sku AND p.product_name=m.product_name AND p.category=m.category WHERE p.product_id IS NULL)
   THROW 51003, 'Product identity mismatch', 1;
 IF EXISTS (SELECT 1 FROM dbo.Products p LEFT JOIN @Map m ON m.product_id=p.product_id WHERE m.product_id IS NULL)
   THROW 51004, 'Unmapped product exists', 1;
 IF EXISTS (SELECT 1 FROM dbo.Products p JOIN @Map m ON p.product_id=m.product_id LEFT JOIN dbo.ProductSubcategories s ON p.subcategory_id=s.subcategory_id AND p.category=s.category WHERE p.subcategory_id IS NOT NULL AND (s.subcategory_id IS NULL OR s.subcategory_name<>m.subcategory_name))
   THROW 51005, 'Conflicting existing assignment', 1;
 INSERT INTO dbo.ProductSubcategories(category,subcategory_name)
 SELECT DISTINCT m.category,m.subcategory_name FROM @Map m
 WHERE NOT EXISTS (SELECT 1 FROM dbo.ProductSubcategories s WITH (UPDLOCK,HOLDLOCK) WHERE s.category=m.category AND s.subcategory_name=m.subcategory_name);
 UPDATE p SET subcategory_id=s.subcategory_id
 FROM dbo.Products p JOIN @Map m ON p.product_id=m.product_id AND p.sku=m.sku AND p.category=m.category
 JOIN dbo.ProductSubcategories s ON s.category=m.category AND s.subcategory_name=m.subcategory_name
 WHERE p.subcategory_id IS NULL;
 IF (SELECT COUNT(*) FROM dbo.Products WHERE subcategory_id IS NOT NULL) <> 150
   THROW 51006, 'Not all products assigned', 1;
 IF EXISTS (SELECT 1 FROM dbo.Products p JOIN @Map m ON p.product_id=m.product_id JOIN dbo.ProductSubcategories s ON s.subcategory_id=p.subcategory_id AND s.category=p.category WHERE s.subcategory_name<>m.subcategory_name)
   THROW 51007, 'Post-load classification mismatch', 1;
 COMMIT TRANSACTION;
 SELECT COUNT(*) AS total_products,COUNT(subcategory_id) AS classified_products FROM dbo.Products;
 SELECT p.category,s.subcategory_name,COUNT(*) AS product_count FROM dbo.Products p JOIN dbo.ProductSubcategories s ON s.subcategory_id=p.subcategory_id AND s.category=p.category GROUP BY p.category,s.subcategory_name ORDER BY p.category,s.subcategory_name;
END TRY
BEGIN CATCH
 IF @@TRANCOUNT>0 ROLLBACK TRANSACTION;
 THROW;
END CATCH;
