package com.vaada.models

/**
 * Transaction type in the Kirana ledger:
 * - CREDIT: Udhaar / सामान दिया (Merchant gave goods on credit; customer owes merchant)
 * - PAYMENT: Jama / भुगतान मिला (Merchant received money; clears balance)
 */
enum class TransactionType {
    CREDIT,
    PAYMENT
}

/**
 * Individual transaction entry within a customer's ledger.
 */
data class LedgerTransaction(
    val id: String,
    val date: String,
    val amount: Double,
    val type: TransactionType,
    val note: String
)

/**
 * Customer account in the Kirana Khata.
 */
data class CustomerAccount(
    val id: String,
    val name: String,
    val phone: String,
    val currentBalance: Double,
    val recentTransactionNote: String,
    val recentTransactionTime: String,
    val transactions: List<LedgerTransaction>
)

/**
 * Isolated demo data source representing realistic Indian Kirana ledger accounts.
 * Clearly identified as sample preview data until Person 3's real backend ledger API is connected.
 */
object KhataSampleData {

    const val IS_DEMO_DATA = true
    const val DATA_LABEL = "डेमो डेटा (Demo Preview)"

    val customers: List<CustomerAccount> = listOf(
        CustomerAccount(
            id = "cust_1",
            name = "रमेश किराना (Ramesh)",
            phone = "+91 98765 43210",
            currentBalance = 450.0,
            recentTransactionNote = "दूध 2L, ब्रेड",
            recentTransactionTime = "आज, 2 घंटे पहले",
            transactions = listOf(
                LedgerTransaction("tx_101", "21 Nov 2026, 12:30 PM", 200.0, TransactionType.CREDIT, "दूध 2L, ब्रेड (Udhaar)"),
                LedgerTransaction("tx_102", "19 Nov 2026, 06:15 PM", 250.0, TransactionType.CREDIT, "चीनी 2kg, चाय पत्ती (Udhaar)"),
                LedgerTransaction("tx_103", "15 Nov 2026, 10:00 AM", 500.0, TransactionType.PAYMENT, "UPI भुगतान (PhonePe)"),
                LedgerTransaction("tx_104", "10 Nov 2026, 08:45 PM", 500.0, TransactionType.CREDIT, "राशन सामान")
            )
        ),
        CustomerAccount(
            id = "cust_2",
            name = "सुरेश भाई (Suresh)",
            phone = "+91 98234 56789",
            currentBalance = 1250.0,
            recentTransactionNote = "चावल 5kg, सरसों तेल 1L",
            recentTransactionTime = "कल शाम 07:15 PM",
            transactions = listOf(
                LedgerTransaction("tx_201", "20 Nov 2026, 07:15 PM", 850.0, TransactionType.CREDIT, "चावल 5kg, सरसों तेल 1L"),
                LedgerTransaction("tx_202", "18 Nov 2026, 11:20 AM", 400.0, TransactionType.CREDIT, "दाल तुअर 2kg, आटा 5kg"),
                LedgerTransaction("tx_203", "12 Nov 2026, 04:30 PM", 1000.0, TransactionType.PAYMENT, "नकद भुगतान (Cash)")
            )
        ),
        CustomerAccount(
            id = "cust_3",
            name = "लक्ष्मी देवी (Lakshmi Devi)",
            phone = "+91 97123 45678",
            currentBalance = 0.0,
            recentTransactionNote = "पूरा हिसाब चुकता (UPI)",
            recentTransactionTime = "2 दिन पहले",
            transactions = listOf(
                LedgerTransaction("tx_301", "19 Nov 2026, 05:00 PM", 600.0, TransactionType.PAYMENT, "पूरा भुगतान (Google Pay)"),
                LedgerTransaction("tx_302", "16 Nov 2026, 01:10 PM", 350.0, TransactionType.CREDIT, "मसाले, नमक, बिस्कुट"),
                LedgerTransaction("tx_303", "14 Nov 2026, 09:40 AM", 250.0, TransactionType.CREDIT, "साबुन, सर्फ पैकेट")
            )
        ),
        CustomerAccount(
            id = "cust_4",
            name = "अनिल कुमार (Anil Kumar)",
            phone = "+91 99456 78123",
            currentBalance = 320.0,
            recentTransactionNote = "दाल मूंग 1kg, बेसन 500g",
            recentTransactionTime = "3 दिन पहले",
            transactions = listOf(
                LedgerTransaction("tx_401", "18 Nov 2026, 06:45 PM", 320.0, TransactionType.CREDIT, "दाल मूंग 1kg, बेसन 500g"),
                LedgerTransaction("tx_402", "10 Nov 2026, 03:00 PM", 500.0, TransactionType.PAYMENT, "नकद जमा")
            )
        ),
        CustomerAccount(
            id = "cust_5",
            name = "गीता बेन (Geeta Ben)",
            phone = "+91 91234 56780",
            currentBalance = 0.0,
            recentTransactionNote = "नकद चुकता किया",
            recentTransactionTime = "4 दिन पहले",
            transactions = listOf(
                LedgerTransaction("tx_501", "17 Nov 2026, 08:00 PM", 150.0, TransactionType.PAYMENT, "नकद चुकता किया"),
                LedgerTransaction("tx_502", "15 Nov 2026, 12:20 PM", 150.0, TransactionType.CREDIT, "घी 200g, माचिस")
            )
        ),
        CustomerAccount(
            id = "cust_6",
            name = "मोहन जी (Mohan Ji)",
            phone = "+91 98989 12345",
            currentBalance = 780.0,
            recentTransactionNote = "देसी घी 1kg",
            recentTransactionTime = "5 दिन पहले",
            transactions = listOf(
                LedgerTransaction("tx_601", "16 Nov 2026, 02:40 PM", 650.0, TransactionType.CREDIT, "देसी घी 1kg (Amul)"),
                LedgerTransaction("tx_602", "14 Nov 2026, 10:15 AM", 130.0, TransactionType.CREDIT, "रिफाइंड तेल 500ml")
            )
        )
    )

    val totalOutstandingBalance: Double
        get() = customers.filter { it.currentBalance > 0 }.sumOf { it.currentBalance }

    val outstandingCustomerCount: Int
        get() = customers.count { it.currentBalance > 0 }

    val totalCustomerCount: Int
        get() = customers.size
}
