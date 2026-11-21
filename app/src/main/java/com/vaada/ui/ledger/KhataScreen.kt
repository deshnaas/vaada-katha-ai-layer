package com.vaada.ui.ledger

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vaada.models.CustomerAccount
import com.vaada.models.KhataSampleData
import com.vaada.ui.theme.KiranaBaakiRed
import com.vaada.ui.theme.KiranaBaakiRedLight
import com.vaada.ui.theme.KiranaBorder
import com.vaada.ui.theme.KiranaCreamBg
import com.vaada.ui.theme.KiranaGreenDark
import com.vaada.ui.theme.KiranaGreenLight
import com.vaada.ui.theme.KiranaGreenPrimary
import com.vaada.ui.theme.KiranaJamaGreen
import com.vaada.ui.theme.KiranaJamaGreenLight
import com.vaada.ui.theme.KiranaMint
import com.vaada.ui.theme.KiranaMintPill
import com.vaada.ui.theme.KiranaSurface
import com.vaada.ui.theme.KiranaTextMuted
import com.vaada.ui.theme.KiranaTextPrimary
import com.vaada.ui.theme.KiranaTextSecondary

@Composable
fun KhataScreen(
    modifier: Modifier = Modifier
) {
    var selectedCustomer by remember { mutableStateOf<CustomerAccount?>(null) }
    var searchQuery by remember { mutableStateOf("") }

    // If a customer is tapped, show their detail screen
    if (selectedCustomer != null) {
        CustomerDetailView(
            customer = selectedCustomer!!,
            onBack = { selectedCustomer = null },
            modifier = modifier
        )
        return
    }

    val allCustomers = remember { KhataSampleData.customers }

    val filteredCustomers = remember(searchQuery) {
        if (searchQuery.isBlank()) {
            allCustomers
        } else {
            val query = searchQuery.trim().lowercase()
            allCustomers.filter {
                it.name.lowercase().contains(query) || it.phone.replace(" ", "").contains(query)
            }
        }
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(KiranaCreamBg)
            .padding(horizontal = 20.dp, vertical = 16.dp)
    ) {
        // ── 1. Screen Header & Demo Indicator ────────────────────────────────
        KhataHeader()

        Spacer(modifier = Modifier.height(16.dp))

        // ── 2. Real-Time Customer Search Field ───────────────────────────────
        CustomerSearchBar(
            query = searchQuery,
            onQueryChange = { searchQuery = it }
        )

        Spacer(modifier = Modifier.height(16.dp))

        // ── 3. Outstanding Balance Summary Card ──────────────────────────────
        OutstandingSummaryCard(
            totalOutstanding = KhataSampleData.totalOutstandingBalance,
            owingCount = KhataSampleData.outstandingCustomerCount
        )

        Spacer(modifier = Modifier.height(20.dp))

        // ── 4. Customer List Header ──────────────────────────────────────────
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = "ग्राहक सूची (Customer List)",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
                color = KiranaTextPrimary
            )
            Text(
                text = "${filteredCustomers.size} खाते",
                style = MaterialTheme.typography.labelSmall,
                color = KiranaTextMuted
            )
        }

        Spacer(modifier = Modifier.height(10.dp))

        // ── 5. Customer Cards List or Empty State ────────────────────────────
        if (filteredCustomers.isEmpty()) {
            EmptySearchState(
                searchQuery = searchQuery,
                onClearSearch = { searchQuery = "" }
            )
        } else {
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                items(filteredCustomers, key = { it.id }) { customer ->
                    CustomerCard(
                        customer = customer,
                        onClick = { selectedCustomer = customer }
                    )
                }

                item {
                    Spacer(modifier = Modifier.height(20.dp))
                }
            }
        }
    }
}

// ── Header Component ─────────────────────────────────────────────────────────

@Composable
private fun KhataHeader() {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(8.dp)
                        .clip(CircleShape)
                        .background(KiranaGreenPrimary)
                )
                Spacer(modifier = Modifier.width(6.dp))
                Text(
                    text = "VAADA KHATA",
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 1.2.sp,
                    color = KiranaGreenPrimary
                )
            }
            Text(
                text = "खाता (Khata)",
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
                color = KiranaTextPrimary
            )
            Text(
                text = "ग्राहक खाते (Customer accounts)",
                style = MaterialTheme.typography.bodyMedium,
                color = KiranaTextSecondary
            )
        }

        // Clearly identified Preview Data Badge
        Surface(
            shape = RoundedCornerShape(12.dp),
            color = KiranaMintPill,
            border = androidx.compose.foundation.BorderStroke(1.dp, KiranaMint)
        ) {
            Text(
                text = KhataSampleData.DATA_LABEL,
                fontSize = 10.sp,
                fontWeight = FontWeight.SemiBold,
                color = KiranaGreenDark,
                modifier = Modifier.padding(horizontal = 8.dp, vertical = 5.dp)
            )
        }
    }
}

// ── Search Field ─────────────────────────────────────────────────────────────

@Composable
private fun CustomerSearchBar(
    query: String,
    onQueryChange: (String) -> Unit
) {
    OutlinedTextField(
        value = query,
        onValueChange = onQueryChange,
        modifier = Modifier
            .fillMaxWidth()
            .height(56.dp),
        placeholder = {
            Text(
                text = "ग्राहक का नाम या मोबाइल खोजें...",
                fontSize = 14.sp,
                color = KiranaTextMuted
            )
        },
        leadingIcon = {
            Icon(
                imageVector = Icons.Default.Search,
                contentDescription = "Search",
                tint = KiranaGreenPrimary,
                modifier = Modifier.size(20.dp)
            )
        },
        trailingIcon = {
            if (query.isNotEmpty()) {
                IconButton(onClick = { onQueryChange("") }) {
                    Icon(
                        imageVector = Icons.Default.Clear,
                        contentDescription = "Clear",
                        tint = KiranaTextMuted,
                        modifier = Modifier.size(18.dp)
                    )
                }
            }
        },
        singleLine = true,
        shape = RoundedCornerShape(16.dp),
        colors = OutlinedTextFieldDefaults.colors(
            focusedContainerColor = KiranaSurface,
            unfocusedContainerColor = KiranaSurface,
            focusedBorderColor = KiranaGreenPrimary,
            unfocusedBorderColor = KiranaBorder,
            focusedTextColor = KiranaTextPrimary,
            unfocusedTextColor = KiranaTextPrimary
        )
    )
}

// ── Outstanding Balance Summary Card ─────────────────────────────────────────

@Composable
private fun OutstandingSummaryCard(
    totalOutstanding: Double,
    owingCount: Int
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = KiranaGreenPrimary),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(20.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "कुल बकाया राशि (Total You'll Receive)",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium,
                    color = KiranaMint
                )
                Surface(
                    shape = RoundedCornerShape(8.dp),
                    color = KiranaGreenLight
                ) {
                    Text(
                        text = "$owingCount ग्राहक",
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold,
                        color = Color.White,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp)
                    )
                }
            }

            Spacer(modifier = Modifier.height(8.dp))

            Text(
                text = "₹${String.format("%.0f", totalOutstanding)}",
                fontSize = 32.sp,
                fontWeight = FontWeight.Bold,
                color = Color.White
            )

            Spacer(modifier = Modifier.height(4.dp))

            Text(
                text = "दुकान का कुल बाज़ार उधार • समय पर वसूली करें",
                fontSize = 11.sp,
                color = KiranaMint.copy(alpha = 0.85f)
            )
        }
    }
}

// ── Customer Row Card ────────────────────────────────────────────────────────

@Composable
private fun CustomerCard(
    customer: CustomerAccount,
    onClick: () -> Unit
) {
    val hasOutstanding = customer.currentBalance > 0

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = KiranaSurface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, KiranaBorder)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            // Customer Avatar & Details
            Row(
                modifier = Modifier.weight(1f),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    modifier = Modifier
                        .size(44.dp)
                        .clip(CircleShape)
                        .background(if (hasOutstanding) KiranaMintPill else KiranaCreamBg),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        text = customer.name.firstOrNull()?.toString() ?: "C",
                        fontSize = 18.sp,
                        fontWeight = FontWeight.Bold,
                        color = KiranaGreenPrimary
                    )
                }

                Spacer(modifier = Modifier.width(12.dp))

                Column {
                    Text(
                        text = customer.name,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = KiranaTextPrimary
                    )
                    Spacer(modifier = Modifier.height(2.dp))
                    Text(
                        text = "${customer.recentTransactionNote} • ${customer.recentTransactionTime}",
                        style = MaterialTheme.typography.labelSmall,
                        color = KiranaTextSecondary,
                        maxLines = 1
                    )
                }
            }

            Spacer(modifier = Modifier.width(8.dp))

            // Balance Chip
            Column(horizontalAlignment = Alignment.End) {
                Text(
                    text = if (hasOutstanding) "₹${String.format("%.0f", customer.currentBalance)}" else "₹0",
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Bold,
                    color = if (hasOutstanding) KiranaBaakiRed else KiranaJamaGreen
                )
                Spacer(modifier = Modifier.height(2.dp))
                Surface(
                    shape = RoundedCornerShape(6.dp),
                    color = if (hasOutstanding) KiranaBaakiRedLight else KiranaJamaGreenLight
                ) {
                    Text(
                        text = if (hasOutstanding) "बाकी (Owes)" else "चुकता (Settled)",
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Bold,
                        color = if (hasOutstanding) KiranaBaakiRed else KiranaJamaGreen,
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                    )
                }
            }
        }
    }
}

// ── Empty Search Result State ────────────────────────────────────────────────

@Composable
private fun EmptySearchState(
    searchQuery: String,
    onClearSearch: () -> Unit
) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 40.dp),
        contentAlignment = Alignment.Center
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(
                imageVector = Icons.Default.Search,
                contentDescription = null,
                tint = KiranaTextMuted,
                modifier = Modifier.size(48.dp)
            )
            Spacer(modifier = Modifier.height(12.dp))
            Text(
                text = "“$searchQuery” नाम का कोई ग्राहक नहीं मिला",
                style = MaterialTheme.typography.bodyLarge,
                fontWeight = FontWeight.Medium,
                color = KiranaTextPrimary
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = "कृपया नाम या फ़ोन नंबर की जांच करें",
                style = MaterialTheme.typography.bodyMedium,
                color = KiranaTextSecondary
            )
            Spacer(modifier = Modifier.height(12.dp))
            TextButton(onClick = onClearSearch) {
                Text("खोज साफ़ करें (Clear Search)", color = KiranaGreenPrimary, fontWeight = FontWeight.Bold)
            }
        }
    }
}
