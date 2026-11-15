# 以 R 套件 arules 重新計算同一份 basket 的所有規則，供 build_guide_examples.py 交叉核對。
# 用法：Rscript crosscheck_arules.R <輸入長表 tid,item> <輸出 csv>
suppressPackageStartupMessages(library(arules))
args <- commandArgs(trailingOnly = TRUE)
d <- read.csv(args[1], fileEncoding = "UTF-8", stringsAsFactors = FALSE)
trans <- as(split(d$item, d$tid), "transactions")
n <- length(trans)
# 支持度門檻設為「至少 1 筆」，前件 + 後件最多 3 項，列出全部規則
rules <- apriori(trans, parameter = list(supp = 0.5 / n, conf = 0, minlen = 2, maxlen = 3),
                 control = list(verbose = FALSE))
q <- quality(rules)
out <- data.frame(
  前件 = gsub(",", ", ", labels(lhs(rules))),
  後件 = gsub(",", ", ", labels(rhs(rules))),
  support = q$support, confidence = q$confidence, lift = q$lift)
write.csv(out, args[2], row.names = FALSE, fileEncoding = "UTF-8")
cat(paste0("arules ", packageVersion("arules")))
