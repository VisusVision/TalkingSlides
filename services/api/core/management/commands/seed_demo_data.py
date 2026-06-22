from __future__ import annotations

import html
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    Category,
    Job,
    LessonComment,
    LessonLike,
    LessonProgress,
    Project,
    TranscriptPage,
    UserProfile,
)
from core.storage_adapter import get_storage_adapter


DEMO_NAMESPACE = "visus_vidlab_demo_seed"
DEMO_PASSWORD = os.environ.get("VISUS_DEMO_PASSWORD", "visus-demo-local")
DEMO_VIDEO_FILENAME = "demo-seed.mp4"
DEMO_PAGE_DURATION_SECONDS = 35.0


@dataclass(frozen=True)
class DemoUser:
    email: str
    display_name: str
    role: str
    bio: str = ""
    is_staff: bool = False


@dataclass(frozen=True)
class DemoPage:
    title: str
    narration: str


@dataclass(frozen=True)
class DemoLesson:
    key: str
    title: str
    description: str
    category: str
    category_slug: str
    language: str
    owner_email: str
    quality: str
    pages: tuple[DemoPage, ...]
    published: bool = True
    moderation_status: str = "approved"
    expected_moderation: str = ""
    moderation_fixture: bool = False
    attach_ocr_image: bool = False


PUBLISHERS: tuple[DemoUser, ...] = (
    DemoUser(
        email="jane.doe.demo@example.com",
        display_name="Jane Doe",
        role="publisher",
        bio="Biology and academic writing instructor.",
    ),
    DemoUser(
        email="ahmet.yilmaz.demo@example.com",
        display_name="Ahmet Yılmaz",
        role="publisher",
        bio="Turkish STEM educator.",
    ),
    DemoUser(
        email="demo.tech.teacher@example.com",
        display_name="Demo Tech Teacher",
        role="teacher",
        bio="Computer science instructor focused on applied AI and data systems.",
    ),
)

STUDENTS: tuple[DemoUser, ...] = (
    DemoUser(email="demo.student.active@example.com", display_name="Active Demo Student", role="student"),
    DemoUser(email="demo.student.struggling@example.com", display_name="Struggling Demo Student", role="student"),
    DemoUser(email="demo.student.commenter@example.com", display_name="Commenter Demo Student", role="student"),
)

STAFF = DemoUser(
    email="demo.staff@example.com",
    display_name="Demo Staff",
    role="teacher",
    bio="Local staff account for reviewing demo moderation fixtures.",
    is_staff=True,
)


def _p(title: str, narration: str) -> DemoPage:
    return DemoPage(title=title, narration=narration)


SHORT_PHOTOSYNTHESIS = (
    _p(
        "What Photosynthesis Means",
        "Photosynthesis is the process plants use to turn light energy into stored chemical energy. "
        "In a leaf, tiny structures inside plant cells capture sunlight and use it to build sugar. "
        "That sugar becomes fuel for growth, repair, and reproduction.",
    ),
    _p(
        "Inputs: Sunlight, Water, and Carbon Dioxide",
        "The process starts with three main inputs. Sunlight provides energy, water usually enters through the roots, "
        "and carbon dioxide enters the leaf through small openings called stomata. These materials meet inside leaf cells.",
    ),
    _p(
        "Outputs: Glucose and Oxygen",
        "The plant uses the captured energy to make glucose, a simple sugar. Oxygen is released as a byproduct, "
        "which is why plant life is closely connected to the air many organisms breathe. The glucose can be used immediately or stored.",
    ),
    _p(
        "Why Plants Matter for Ecosystems",
        "Photosynthesis supports food webs because plants are producers. Animals, fungi, and many microorganisms depend on "
        "the energy that plants first store in sugars. The process also links the carbon cycle with oxygen production.",
    ),
    _p(
        "Simple Recap",
        "A useful summary is: light energy plus water plus carbon dioxide can produce glucose and oxygen. "
        "The details are complex, but the central idea is that plants convert sunlight into usable energy. "
        "When you see a healthy leaf, you are seeing a small energy system at work.",
    ),
)


TURKISH_PHOTOSYNTHESIS = (
    _p(
        "Fotosentez Nedir?",
        "Fotosentez, bitkilerin ışık enerjisini kimyasal enerjiye dönüştürdüğü temel bir süreçtir. "
        "Bu süreç sayesinde bitki kendi besinini üretir ve büyüme için gerekli enerjiyi depolar.",
    ),
    _p(
        "Klorofilin Görevi",
        "Klorofil, yapraklara yeşil rengini veren pigmenttir. Aynı zamanda güneş ışığını yakalayarak "
        "fotosentezin ilk adımını başlatır. Klorofil en çok kloroplast adı verilen yapılarda bulunur.",
    ),
    _p(
        "Işık Enerjisinin Yakalanması",
        "Bitki hücreleri ışığı doğrudan besin olarak kullanmaz. Önce ışık enerjisi yakalanır ve hücre içinde "
        "kullanılabilecek kimyasal enerjiye dönüştürülür. Bu dönüşüm, fotosentezin enerji üretimi açısından en önemli bölümüdür.",
    ),
    _p(
        "Su Nasıl Kullanılır?",
        "Bitki suyu çoğunlukla kökleriyle topraktan alır. Su, gövde içindeki iletim dokuları sayesinde yapraklara taşınır. "
        "Fotosentez sırasında su molekülleri parçalanır ve süreçte oksijen açığa çıkabilir.",
    ),
    _p(
        "Karbondioksit Nereden Gelir?",
        "Karbondioksit havada bulunan bir gazdır ve yaprağın yüzeyindeki gözeneklerden içeri girer. "
        "Bu gözeneklere stoma denir. Bitki, karbondioksitteki karbonu glikoz üretmek için kullanır.",
    ),
    _p(
        "Glikoz Üretimi",
        "Glikoz, bitkinin enerji kaynağı olarak kullanabildiği basit bir şekerdir. Bitki glikozu hemen kullanabilir, "
        "nişasta olarak depolayabilir veya yeni hücre yapılarının üretiminde değerlendirebilir.",
    ),
    _p(
        "Oksijenin Açığa Çıkması",
        "Fotosentez sırasında oksijen atmosfere verilir. Bu oksijen, insanlar ve birçok canlı için solunumda gereklidir. "
        "Bu nedenle fotosentez yalnızca bitkiler için değil, ekosistemin tamamı için önemlidir.",
    ),
    _p(
        "Günlük Hayattan Bir Örnek",
        "Bir saksı bitkisini pencere kenarına koyduğunuzda daha iyi geliştiğini görebilirsiniz. "
        "Bunun nedeni, bitkinin daha fazla ışık alarak fotosentez için daha uygun koşullara sahip olmasıdır.",
    ),
    _p(
        "Sık Yapılan Bir Yanlış Anlama",
        "Fotosentez, bitkinin sadece gündüz yaptığı basit bir nefes alma işlemi değildir. "
        "Aslında bitki hem fotosentez yapar hem de canlı hücreleri için solunum gerçekleştirir. "
        "Bu iki süreç farklı amaçlara hizmet eder.",
    ),
    _p(
        "Ekosistem İçindeki Rol",
        "Fotosentez, besin zincirinin başlangıç noktalarından biridir. Bitkiler üretici canlılar olduğu için "
        "diğer canlıların enerji ihtiyacının büyük kısmı dolaylı olarak fotosenteze dayanır.",
    ),
    _p(
        "Özet",
        "Fotosentez için ışık, su ve karbondioksit gerekir. Sonuçta glikoz üretilir ve oksijen açığa çıkar. "
        "Bu süreç bitkilerin büyümesini sağlar ve canlı yaşamı için temel bir enerji akışı oluşturur.",
    ),
)


CELL_STRUCTURE = (
    _p(
        "Why Study Cells?",
        "Cells are the smallest living units that can carry out the processes of life. "
        "When we understand cells, we can explain growth, disease, inheritance, and energy use in organisms. "
        "This lesson builds a map of the major structures inside cells.",
    ),
    _p(
        "Cell Theory",
        "Cell theory has three core ideas: all living things are made of cells, cells are the basic unit of life, "
        "and new cells come from existing cells. These ideas connected microscope observations with modern biology. "
        "They also explain why cellular damage can affect whole organisms.",
    ),
    _p(
        "Two Broad Cell Types",
        "Biologists often compare prokaryotic and eukaryotic cells. Prokaryotic cells are generally smaller and lack a membrane-bound nucleus. "
        "Eukaryotic cells include plant, animal, fungal, and protist cells, and they contain specialized organelles.",
    ),
    _p(
        "Prokaryotic Cells",
        "Bacteria and archaea are prokaryotes. Their DNA is found in a region called the nucleoid rather than inside a nucleus. "
        "They still perform essential life processes, including metabolism, reproduction, and response to the environment.",
    ),
    _p(
        "Eukaryotic Cells",
        "Eukaryotic cells contain internal membrane systems that organize cellular work. "
        "A nucleus stores genetic information, while other organelles process energy, build proteins, and move materials. "
        "This organization supports larger and more complex cells.",
    ),
    _p(
        "The Nucleus",
        "The nucleus stores most of a eukaryotic cell's DNA. It is surrounded by a nuclear envelope with pores that regulate traffic. "
        "Inside the nucleus, DNA instructions are copied into RNA before many proteins are made.",
    ),
    _p(
        "Chromatin and Chromosomes",
        "DNA in the nucleus is packaged with proteins as chromatin. During cell division, chromatin condenses into visible chromosomes. "
        "This packaging helps the cell store long DNA molecules while still accessing genes when needed.",
    ),
    _p(
        "The Nucleolus",
        "The nucleolus is a dense region inside the nucleus. It helps produce ribosomal RNA and assemble ribosome parts. "
        "Because ribosomes build proteins, the nucleolus indirectly supports almost every cellular function.",
    ),
    _p(
        "Ribosomes",
        "Ribosomes read messenger RNA and assemble amino acids into proteins. They can float freely in the cytoplasm or attach to rough endoplasmic reticulum. "
        "A cell with heavy protein production often has many ribosomes.",
    ),
    _p(
        "Protein Synthesis in Context",
        "A gene does not become a trait in one step. DNA is transcribed into RNA, and ribosomes translate that RNA into protein. "
        "The final protein may become an enzyme, a structural fiber, or a signal molecule.",
    ),
    _p(
        "Endoplasmic Reticulum Overview",
        "The endoplasmic reticulum, or ER, is a network of membranes connected to the nuclear envelope. "
        "It provides space for building, folding, and transporting molecules. There are two main forms: rough ER and smooth ER.",
    ),
    _p(
        "Rough ER",
        "Rough ER is covered with ribosomes, giving it a dotted appearance under a microscope. "
        "It helps produce proteins that will be secreted, embedded in membranes, or sent to certain organelles. "
        "Cells that make many exported proteins often have extensive rough ER.",
    ),
    _p(
        "Smooth ER",
        "Smooth ER lacks attached ribosomes. It is involved in lipid production, detoxification, and calcium storage. "
        "In some muscle cells, specialized smooth ER helps manage calcium signals for contraction.",
    ),
    _p(
        "Golgi Apparatus",
        "The Golgi apparatus modifies, sorts, and packages molecules that arrive from the ER. "
        "It works like a shipping center: proteins and lipids are tagged and sent to their destinations. "
        "Vesicles carry these packages through the cell.",
    ),
    _p(
        "Vesicles",
        "Vesicles are small membrane-bound sacs that move materials. They can transport proteins from the ER to the Golgi, "
        "carry enzymes to lysosomes, or bring substances to the cell membrane. Vesicle traffic keeps cellular compartments connected.",
    ),
    _p(
        "Mitochondria",
        "Mitochondria convert energy from food molecules into ATP, a usable energy currency. "
        "They have inner and outer membranes, and the inner membrane has folds that increase surface area. "
        "Cells with high energy demand often contain many mitochondria.",
    ),
    _p(
        "ATP and Cellular Work",
        "ATP powers many cellular tasks, including movement, active transport, and chemical synthesis. "
        "Mitochondria do not create energy from nothing; they transform energy stored in molecules. "
        "This is why nutrition and oxygen delivery matter for active tissues.",
    ),
    _p(
        "Chloroplasts",
        "Chloroplasts are found in plants and algae. They capture light energy and use it to build sugars through photosynthesis. "
        "Like mitochondria, chloroplasts have their own DNA and internal membranes.",
    ),
    _p(
        "Lysosomes",
        "Lysosomes contain enzymes that break down worn-out cell parts, large molecules, and some materials brought into the cell. "
        "They are especially important in animal cells. Their enzymes work best in an acidic internal environment.",
    ),
    _p(
        "Peroxisomes",
        "Peroxisomes help break down fatty acids and detoxify certain harmful molecules. "
        "They produce and then break down hydrogen peroxide, which can damage cells if it accumulates. "
        "This makes peroxisomes part of the cell's chemical safety system.",
    ),
    _p(
        "The Cell Membrane",
        "The cell membrane forms a selective boundary around the cell. "
        "It is made mostly of phospholipids and proteins. The membrane allows some substances to pass while controlling others.",
    ),
    _p(
        "Membrane Proteins",
        "Membrane proteins can act as channels, pumps, receptors, or markers. "
        "They help cells move substances, receive signals, and identify one another. "
        "A membrane is therefore active and responsive, not just a passive wrapper.",
    ),
    _p(
        "Cytoplasm",
        "Cytoplasm includes the fluid and many structures inside the cell membrane but outside the nucleus. "
        "Many chemical reactions happen there. Organelles are suspended within this environment and interact through transport and signaling.",
    ),
    _p(
        "Cytoskeleton",
        "The cytoskeleton is a network of protein fibers that gives shape and organization to the cell. "
        "It helps move organelles, supports cell division, and allows some cells to move. "
        "The major components include microtubules, microfilaments, and intermediate filaments.",
    ),
    _p(
        "Centrosomes and Cell Division",
        "In many animal cells, the centrosome organizes microtubules. "
        "During cell division, microtubules help separate chromosomes into daughter cells. "
        "Accurate separation is essential because each new cell needs the correct genetic information.",
    ),
    _p(
        "Vacuoles",
        "Vacuoles store water, ions, nutrients, and waste products. Plant cells often have a large central vacuole. "
        "This structure helps maintain internal pressure, which supports the plant's shape.",
    ),
    _p(
        "Cell Wall",
        "Plant cells, fungal cells, and many prokaryotes have cell walls outside the membrane. "
        "In plants, the wall is rich in cellulose and provides support. "
        "Animal cells do not have cell walls, which allows more flexible shapes.",
    ),
    _p(
        "Plant Cells Compared with Animal Cells",
        "Plant cells usually contain chloroplasts, a large central vacuole, and a cellulose cell wall. "
        "Animal cells usually lack these structures but often have lysosomes and centrioles. "
        "Both cell types share nuclei, mitochondria, membranes, ribosomes, ER, and Golgi apparatus.",
    ),
    _p(
        "Specialized Cells",
        "Cells can specialize for different roles. Nerve cells transmit signals, muscle cells contract, and red blood cells carry oxygen. "
        "Specialization often changes which organelles are most abundant.",
    ),
    _p(
        "Example: Pancreatic Cells",
        "Some pancreatic cells secrete digestive enzymes. They contain abundant rough ER and Golgi apparatus because they produce and package proteins. "
        "This example shows how organelle abundance matches cellular function.",
    ),
    _p(
        "Example: Leaf Cells",
        "Leaf cells involved in photosynthesis contain many chloroplasts. They also have vacuoles and cell walls that support plant structure. "
        "Their organelles help explain how a leaf captures light and exchanges gases.",
    ),
    _p(
        "Example: Muscle Cells",
        "Muscle cells need large amounts of ATP to contract repeatedly. They often contain many mitochondria and specialized membranes for calcium control. "
        "Structure and function are linked at the cellular level.",
    ),
    _p(
        "How Organelles Cooperate",
        "A protein might begin as genetic information in the nucleus, be built by ribosomes, folded in rough ER, processed by the Golgi, and shipped in a vesicle. "
        "This pathway shows that organelles work as a coordinated system.",
    ),
    _p(
        "What Happens When Organelles Fail?",
        "If an organelle cannot do its job, the effects can spread. Problems in mitochondria can reduce available ATP, while defects in lysosomes can cause materials to accumulate. "
        "Cell biology helps explain many disease mechanisms.",
    ),
    _p(
        "Microscopes and Evidence",
        "Many organelles were discovered and studied through microscopy. Light microscopes show cells and some larger structures, while electron microscopes reveal fine internal detail. "
        "Modern cell biology combines imaging with molecular evidence.",
    ),
    _p(
        "Common Misconception",
        "A cell is not a bag of liquid with floating parts. It is organized, regulated, and constantly active. "
        "Organelles are positioned and connected in ways that make cellular work more efficient.",
    ),
    _p(
        "Recap: Big Picture",
        "Cells use compartments to organize life processes. The nucleus stores instructions, ribosomes make proteins, mitochondria produce ATP, and membranes regulate movement. "
        "Plant cells add structures that support photosynthesis and rigidity.",
    ),
    _p(
        "Quiz Question 1",
        "Which organelle is most directly involved in producing ATP from food molecules? "
        "A strong answer should name mitochondria and explain that they transform energy into a usable cellular form.",
    ),
    _p(
        "Quiz Question 2",
        "Why might a protein-secreting cell have a large amount of rough ER and Golgi apparatus? "
        "The rough ER helps build and fold proteins, while the Golgi modifies and packages them for transport.",
    ),
    _p(
        "Final Reflection",
        "To understand a cell, always connect structure with function. Ask what a structure is made of, what job it performs, and how it works with other parts of the cell. "
        "That habit turns organelle names into a usable biological model.",
    ),
)


NEURAL_NETWORK_OPTIMIZATION = (
    _p(
        "Optimization in Machine Learning",
        "Training a neural network is an optimization problem: we search for parameter values that reduce prediction error. "
        "The model may contain millions of weights, so the search must be guided by mathematical signals rather than manual adjustment.",
    ),
    _p(
        "Loss Functions",
        "A loss function converts model mistakes into a number that can be minimized. "
        "For classification, cross-entropy is common because it penalizes confident wrong predictions. "
        "For regression, mean squared error is often used when larger errors should count disproportionately more.",
    ),
    _p(
        "Gradients",
        "A gradient points in the direction of steepest increase for the loss. "
        "Optimization methods usually move parameters in the opposite direction. "
        "The gradient is local, so it tells us how the loss behaves near the current parameter values, not everywhere.",
    ),
    _p(
        "Backpropagation",
        "Backpropagation applies the chain rule to compute gradients efficiently across layers. "
        "Each layer contributes partial derivatives that are combined from the output layer backward. "
        "Without this algorithm, training deep networks would be computationally impractical.",
    ),
    _p(
        "Learning Rate",
        "The learning rate controls the size of each parameter update. "
        "If it is too small, training can become slow and appear stalled. If it is too large, the model can overshoot useful regions and fail to converge.",
    ),
    _p(
        "Stochastic Gradient Descent",
        "Stochastic gradient descent estimates the gradient using a subset of examples rather than the full dataset. "
        "This makes each step cheaper and adds noise that can sometimes help escape shallow local patterns. "
        "The tradeoff is that progress becomes less smooth.",
    ),
    _p(
        "Momentum",
        "Momentum accumulates a moving average of recent update directions. "
        "It can speed movement along consistent slopes and reduce oscillation across narrow valleys. "
        "However, excessive momentum can carry parameters past a good solution.",
    ),
    _p(
        "Adaptive Optimizers",
        "Methods such as Adam adjust update sizes based on running estimates of gradient moments. "
        "They often work well with minimal tuning, especially early in experimentation. "
        "They are not magic; validation behavior and generalization still need to be checked.",
    ),
    _p(
        "Batch Size",
        "Batch size affects gradient noise, memory use, and training dynamics. "
        "Small batches produce noisier estimates but can update more frequently. Large batches can use hardware efficiently but may require learning-rate adjustment.",
    ),
    _p(
        "Overfitting",
        "A model overfits when it performs well on training data but poorly on new data. "
        "Optimization can reduce training loss while still harming generalization. "
        "This is why validation metrics must be monitored separately from training loss.",
    ),
    _p(
        "Regularization",
        "Regularization techniques constrain the model so it does not simply memorize the training set. "
        "Weight decay, dropout, data augmentation, and early stopping all reduce different forms of overfitting. "
        "The best choice depends on data size, model capacity, and task structure.",
    ),
    _p(
        "Vanishing and Exploding Gradients",
        "In deep networks, gradients can become extremely small or extremely large as they pass through many layers. "
        "Vanishing gradients slow learning in early layers, while exploding gradients destabilize updates. "
        "Initialization, normalization, residual connections, and gradient clipping can help.",
    ),
    _p(
        "Optimization Pitfalls",
        "A falling loss curve does not guarantee a useful model. Data leakage, label noise, class imbalance, and poor validation design can hide serious problems. "
        "Optimization should be evaluated as part of the full modeling pipeline.",
    ),
    _p(
        "Debugging Training Runs",
        "When training fails, inspect data examples, loss scale, gradient norms, and learning-rate behavior. "
        "A tiny subset overfit test can reveal whether the model and training loop are capable of learning at all. "
        "This diagnostic step is often faster than changing architectures blindly.",
    ),
    _p(
        "Recap",
        "Neural network optimization combines loss functions, gradients, update rules, and generalization controls. "
        "The concepts are compact but dense, so learners usually need concrete examples and visual intuition. "
        "A good lesson should slow down around gradients and learning-rate tradeoffs.",
    ),
)


VAGUE_DATABASE_NOTES = (
    _p("Tables", "Tables store data. Rows are records. Columns are fields."),
    _p("Keys", "Keys identify rows. Keys connect tables. Use keys carefully."),
    _p("Queries", "Queries get data. SQL is used. SELECT gets rows."),
    _p("Relationships", "Relationships connect information. One-to-many is common. Many-to-many needs another table."),
    _p("Indexes", "Indexes make searches faster. They can slow writes. Use them when needed."),
    _p("Normalization", "Normalization reduces repetition. It splits data. It helps consistency."),
    _p("Transactions", "Transactions group changes. Commit saves. Rollback cancels."),
    _p("Problems", "Bad design causes duplicates. Queries become confusing. More examples are needed."),
)


ACADEMIC_ABSTRACT = (
    _p(
        "Purpose of an Abstract",
        "An academic abstract gives readers a compact preview of the whole study. "
        "It should identify the topic, the problem, the method, the main findings, and the contribution. "
        "A strong abstract helps readers decide whether the full paper is relevant.",
    ),
    _p(
        "Start with the Research Problem",
        "The opening sentence should establish the issue or gap the study addresses. "
        "Avoid beginning with broad claims that could apply to any field. "
        "The reader should quickly understand why the research question matters.",
    ),
    _p(
        "Name the Method",
        "A useful abstract briefly states how the study was conducted. "
        "This might include a dataset, experiment, survey, textual analysis, or theoretical framework. "
        "The method should be specific enough to make the evidence credible.",
    ),
    _p(
        "Summarize the Findings",
        "The findings are usually the most important part of the abstract. "
        "Use concrete language rather than saying the paper discusses results. "
        "If possible, include the direction or scale of the main result.",
    ),
    _p(
        "Explain the Contribution",
        "The contribution tells readers what the study adds. "
        "It might refine a theory, provide new evidence, introduce a method, or clarify a debate. "
        "This section should connect the findings back to the research problem.",
    ),
    _p(
        "Common Mistakes",
        "Weak abstracts often stay too general, hide the findings, or spend too much space on background. "
        "Another common mistake is promising significance without explaining the actual contribution. "
        "Readers need substance, not only topic labels.",
    ),
    _p(
        "Example Breakdown",
        "Consider an abstract about first-year writing feedback. The problem is inconsistent student revision, "
        "the method is a comparison of annotated drafts, the finding is improved revision depth after targeted comments, "
        "and the contribution is a practical feedback model for instructors.",
    ),
    _p(
        "Revision Checklist",
        "After drafting an abstract, check for five elements: problem, method, findings, contribution, and clarity. "
        "Remove sentences that only announce the paper's structure. "
        "Every sentence should help a reader understand the study.",
    ),
    _p(
        "Conclusion",
        "A strong abstract is short but complete. It does not replace the paper; it gives a reliable map of the paper. "
        "The best abstracts are specific, evidence-based, and easy to scan.",
    ),
)


WORLD_WAR_II_CONTEXT = (
    _p(
        "Historical Framing",
        "World War II is studied as a global conflict shaped by diplomacy, ideology, economics, and military decisions. "
        "A responsible lesson uses neutral language and distinguishes evidence from interpretation.",
    ),
    _p(
        "Causes and Conditions",
        "The war did not begin from a single event alone. Economic instability, aggressive expansion, unresolved tensions after World War I, "
        "and failures of collective security all contributed to the crisis.",
    ),
    _p(
        "Global Scope",
        "The conflict affected Europe, Africa, Asia, the Pacific, and the Americas in different ways. "
        "Studying multiple regions helps students avoid a narrow timeline and understand how local experiences differed.",
    ),
    _p(
        "Civilian Impact",
        "Civilians experienced displacement, occupation, rationing, forced labor, and loss. "
        "This lesson discusses those consequences in non-graphic terms so students can understand historical significance without sensational detail.",
    ),
    _p(
        "Sources and Evidence",
        "Historians use government records, diaries, photographs, speeches, maps, and survivor testimony. "
        "Each source has a perspective and must be evaluated for context, purpose, and reliability.",
    ),
    _p(
        "Technology and Strategy",
        "New technologies changed communication, transportation, production, and military planning. "
        "Students should connect technology to broader social and political consequences rather than treating it as isolated invention.",
    ),
    _p(
        "Aftermath",
        "The war reshaped international institutions, borders, economies, and public memory. "
        "Postwar reconstruction and human rights discussions became central to the second half of the twentieth century.",
    ),
    _p(
        "Historical Responsibility",
        "Learning about war requires careful attention to human consequences and evidence-based reasoning. "
        "The goal is not to glorify conflict, but to understand causes, choices, and lasting effects.",
    ),
    _p(
        "Recap",
        "World War II should be analyzed through causes, global scope, civilian experience, evidence, and aftermath. "
        "A balanced lesson helps students ask historical questions while avoiding graphic or inflammatory presentation.",
    ),
)


LESSONS: tuple[DemoLesson, ...] = (
    DemoLesson(
        key="photosynthesis-short-en",
        title="Introduction to Photosynthesis",
        description="A short introductory biology lesson explaining how plants convert light into stored chemical energy.",
        category="Biology",
        category_slug="biology",
        language="en",
        owner_email="jane.doe.demo@example.com",
        quality="good",
        pages=SHORT_PHOTOSYNTHESIS,
    ),
    DemoLesson(
        key="photosynthesis-medium-tr",
        title="Bitkilerde Fotosentez ve Enerji Üretimi",
        description="Orta uzunlukta Türkçe fen dersi: fotosentezin girdileri, çıktıları ve ekosistemdeki rolü.",
        category="Fen Bilimleri",
        category_slug="fen-bilimleri",
        language="tr",
        owner_email="ahmet.yilmaz.demo@example.com",
        quality="medium",
        pages=TURKISH_PHOTOSYNTHESIS,
    ),
    DemoLesson(
        key="cell-structure-long-en",
        title="Cell Structure and Organelles",
        description="A long biology lesson with enough transcript volume to exercise lesson intelligence chunking.",
        category="Biology",
        category_slug="biology",
        language="en",
        owner_email="jane.doe.demo@example.com",
        quality="good",
        pages=CELL_STRUCTURE,
    ),
    DemoLesson(
        key="neural-network-optimization",
        title="Introduction to Neural Network Optimization",
        description="A dense technical lesson covering loss functions, gradients, backpropagation, and training pitfalls.",
        category="Computer Science",
        category_slug="computer-science",
        language="en",
        owner_email="demo.tech.teacher@example.com",
        quality="complex",
        pages=NEURAL_NETWORK_OPTIMIZATION,
    ),
    DemoLesson(
        key="vague-database-notes",
        title="Vague Notes About Databases",
        description="An intentionally underdeveloped lesson fixture with sparse explanations and missing examples.",
        category="Computer Science",
        category_slug="computer-science",
        language="en",
        owner_email="demo.tech.teacher@example.com",
        quality="poor",
        pages=VAGUE_DATABASE_NOTES,
    ),
    DemoLesson(
        key="academic-abstract",
        title="How to Write a Strong Academic Abstract",
        description="A structured academic writing lesson with clear purpose, method, findings, and revision guidance.",
        category="Academic Writing",
        category_slug="academic-writing",
        language="en",
        owner_email="jane.doe.demo@example.com",
        quality="well_structured",
        pages=ACADEMIC_ABSTRACT,
    ),
    DemoLesson(
        key="wwii-context",
        title="Understanding World War II in Historical Context",
        description="A neutral educational history lesson discussing causes, consequences, and source evaluation.",
        category="History",
        category_slug="history",
        language="en",
        owner_email="jane.doe.demo@example.com",
        quality="sensitive_educational",
        pages=WORLD_WAR_II_CONTEXT,
    ),
)


MODERATION_FIXTURES: tuple[DemoLesson, ...] = (
    DemoLesson(
        key="moderation-offensive-language",
        title="Moderation Test: Offensive Language",
        description="Safe local fixture for testing mild offensive language detection.",
        category="Demo Moderation",
        category_slug="demo-moderation",
        language="en",
        owner_email="demo.tech.teacher@example.com",
        quality="moderation_fixture",
        pages=(
            _p(
                "Mild Offensive Wording",
                "This fictional classroom note contains one mild profanity example: the word shit appears as text that should be revised. "
                "The fixture is intentionally limited and avoids targeted harassment.",
            ),
        ),
        published=False,
        moderation_status="not_scanned",
        expected_moderation="review_or_block",
        moderation_fixture=True,
    ),
    DemoLesson(
        key="moderation-ocr-text-image",
        title="Moderation Test: OCR Text Image",
        description="Runtime-generated image fixture containing review text for OCR moderation smoke testing.",
        category="Demo Moderation",
        category_slug="demo-moderation",
        language="en",
        owner_email="demo.tech.teacher@example.com",
        quality="moderation_fixture",
        pages=(
            _p(
                "OCR Image Fixture",
                "This page attaches a generated image containing the text MODERATION TEST: review this text. "
                "The narration itself is safe; the image is for OCR pipeline verification.",
            ),
        ),
        published=False,
        moderation_status="not_scanned",
        expected_moderation="ocr_review_if_enabled",
        moderation_fixture=True,
        attach_ocr_image=True,
    ),
    DemoLesson(
        key="moderation-student-safety-concern",
        title="Moderation Test: Student Safety Concern",
        description="Safe help-seeking student safety fixture with no methods or instructions.",
        category="Demo Moderation",
        category_slug="demo-moderation",
        language="en",
        owner_email="demo.tech.teacher@example.com",
        quality="moderation_fixture",
        pages=(
            _p(
                "Fictional Safety Concern",
                "A fictional student writes that they feel unsafe and need support from a trusted adult immediately. "
                "The lesson asks the instructor to pause, listen, and connect the student with appropriate school or professional support.",
            ),
        ),
        published=False,
        moderation_status="not_scanned",
        expected_moderation="review_or_escalate",
        moderation_fixture=True,
    ),
    DemoLesson(
        key="moderation-threat-language",
        title="Moderation Test: Threat Language",
        description="Non-graphic fictional threat-language fixture for local moderation checks.",
        category="Demo Moderation",
        category_slug="demo-moderation",
        language="en",
        owner_email="demo.tech.teacher@example.com",
        quality="moderation_fixture",
        pages=(
            _p(
                "Threat-Language Example",
                "A fictional line in a classroom safety scenario says: I will kill you if you speak during the activity. "
                "No operational detail is included; this is only a moderation test sentence.",
            ),
        ),
        published=False,
        moderation_status="not_scanned",
        expected_moderation="review_or_block",
        moderation_fixture=True,
    ),
    DemoLesson(
        key="moderation-adult-placeholder",
        title="Moderation Test: Adult Content Placeholder",
        description="Policy-label-only adult content fixture with no explicit sexual description.",
        category="Demo Moderation",
        category_slug="demo-moderation",
        language="en",
        owner_email="demo.tech.teacher@example.com",
        quality="moderation_fixture",
        pages=(
            _p(
                "Policy Label Placeholder",
                "This fixture uses the policy label explicit sexual content placeholder only. "
                "It does not describe sexual acts, images, or scenarios.",
            ),
        ),
        published=False,
        moderation_status="not_scanned",
        expected_moderation="review_or_block",
        moderation_fixture=True,
    ),
    DemoLesson(
        key="moderation-historical-war-impact",
        title="Historical Education: War and Civilian Impact",
        description="Neutral educational discussion of war consequences in non-graphic terms.",
        category="Demo Moderation",
        category_slug="demo-moderation",
        language="en",
        owner_email="jane.doe.demo@example.com",
        quality="moderation_fixture",
        pages=(
            _p(
                "Civilian Impact in Historical Study",
                "This educational history fixture discusses war, displacement, and killed civilians in a neutral academic context. "
                "The purpose is source analysis and historical understanding, not sensational description.",
            ),
        ),
        published=False,
        moderation_status="not_scanned",
        expected_moderation="allowed_or_review",
        moderation_fixture=True,
    ),
    DemoLesson(
        key="moderation-mental-health-support",
        title="Mental Health Education: Finding Support",
        description="Supportive educational guidance about finding help from trusted adults and professionals.",
        category="Demo Moderation",
        category_slug="demo-moderation",
        language="en",
        owner_email="jane.doe.demo@example.com",
        quality="moderation_fixture",
        pages=(
            _p(
                "Finding Support",
                "If a learner feels overwhelmed or unsafe, they should talk with a trusted adult, counselor, doctor, or local emergency support service. "
                "This lesson focuses on connection, safety planning with professionals, and reducing isolation.",
            ),
        ),
        published=False,
        moderation_status="not_scanned",
        expected_moderation="allowed_or_review",
        moderation_fixture=True,
    ),
)

MODERATION_FIXTURE_KEYS = frozenset(fixture.key for fixture in MODERATION_FIXTURES)


COMMENT_ACTIVITY = {
    "academic-abstract": (
        ("demo.student.active@example.com", "The example breakdown made the topic easier to understand."),
        ("demo.student.commenter@example.com", "The lesson was clear and well organized."),
    ),
    "photosynthesis-short-en": (
        ("demo.student.active@example.com", "The recap helped me connect oxygen release with glucose production."),
        ("demo.student.commenter@example.com", "Clear explanation for a first biology lesson."),
    ),
    "cell-structure-long-en": (
        ("demo.student.active@example.com", "The organelle examples made the long lesson easier to follow."),
    ),
    "vague-database-notes": (
        ("demo.student.struggling@example.com", "I need more examples before the database relationships make sense."),
    ),
    "neural-network-optimization": (
        ("demo.student.struggling@example.com", "The neural network lesson moves too fast after the gradient section."),
        ("demo.student.commenter@example.com", "Can you explain the learning rate with a simple analogy?"),
    ),
    "photosynthesis-medium-tr": (
        ("demo.student.commenter@example.com", "Günlük hayattan verilen örnek konuyu daha anlaşılır yaptı."),
    ),
}

PROGRESS_ACTIVITY = {
    "academic-abstract": {
        "demo.student.active@example.com": 95,
        "demo.student.commenter@example.com": 88,
        "demo.student.struggling@example.com": 82,
    },
    "photosynthesis-short-en": {
        "demo.student.active@example.com": 92,
        "demo.student.commenter@example.com": 86,
        "demo.student.struggling@example.com": 74,
    },
    "cell-structure-long-en": {
        "demo.student.active@example.com": 84,
        "demo.student.commenter@example.com": 78,
        "demo.student.struggling@example.com": 61,
    },
    "photosynthesis-medium-tr": {
        "demo.student.active@example.com": 73,
        "demo.student.commenter@example.com": 66,
        "demo.student.struggling@example.com": 58,
    },
    "wwii-context": {
        "demo.student.active@example.com": 76,
        "demo.student.commenter@example.com": 69,
        "demo.student.struggling@example.com": 55,
    },
    "neural-network-optimization": {
        "demo.student.active@example.com": 55,
        "demo.student.commenter@example.com": 41,
        "demo.student.struggling@example.com": 28,
    },
    "vague-database-notes": {
        "demo.student.active@example.com": 48,
        "demo.student.commenter@example.com": 36,
        "demo.student.struggling@example.com": 22,
    },
}

LIKE_ACTIVITY = {
    "academic-abstract": ("demo.student.active@example.com", "demo.student.commenter@example.com"),
    "photosynthesis-short-en": ("demo.student.active@example.com", "demo.student.commenter@example.com"),
    "cell-structure-long-en": ("demo.student.active@example.com",),
    "photosynthesis-medium-tr": ("demo.student.commenter@example.com",),
}


class Command(BaseCommand):
    help = "Seed reusable realistic VISUS VidLab demo users, lessons, analytics, and moderation fixtures."

    def add_arguments(self, parser):
        parser.add_argument("--reset-demo", action="store_true", help="Delete previously seeded demo projects/users first.")
        parser.add_argument(
            "--with-moderation-fixtures",
            action="store_true",
            help="Create safe moderation fixture projects, including a generated OCR image fixture.",
        )
        parser.add_argument(
            "--with-analytics-activity",
            action="store_true",
            help="Explicitly seed analytics activity. Analytics is seeded by default unless --without-analytics-activity is used.",
        )
        parser.add_argument(
            "--without-analytics-activity",
            action="store_true",
            help="Create lessons/accounts but skip progress, likes, and comments.",
        )
        parser.add_argument(
            "--run-moderation",
            action="store_true",
            help="Run the existing local moderation scan for demo moderation fixtures and print statuses.",
        )
        parser.add_argument(
            "--run-intelligence",
            action="store_true",
            help="Schedule lesson and creator analytics intelligence for demo data without waiting for background enhancement.",
        )

    def handle(self, *args, **options):
        include_moderation = bool(options["with_moderation_fixtures"] or options["run_moderation"])
        include_analytics = not bool(options["without_analytics_activity"])

        if options["reset_demo"]:
            self._reset_demo()

        with transaction.atomic():
            users = self._seed_users()
            projects = self._seed_lessons(
                users,
                include_moderation=include_moderation,
            )
            if include_analytics:
                analytics_counts = self._seed_analytics(users, projects)
            else:
                analytics_counts = {"progress": 0, "likes": 0, "comments": 0}

        moderation_results: list[dict[str, Any]] = []
        if include_moderation:
            self._ensure_ocr_fixture_image(projects)
        if options["run_moderation"]:
            moderation_results = self._run_moderation(projects)

        intelligence_results: list[dict[str, Any]] = []
        if options["run_intelligence"]:
            intelligence_results = self._run_intelligence(projects, users)

        cache.clear()
        self._print_summary(
            users=users,
            projects=projects,
            analytics_counts=analytics_counts,
            include_moderation=include_moderation,
            moderation_results=moderation_results,
            intelligence_results=intelligence_results,
        )

    def _reset_demo(self) -> None:
        demo_emails = _demo_emails()
        demo_titles = [lesson.title for lesson in (*LESSONS, *MODERATION_FIXTURES)]
        projects = Project.objects.filter(
            Q(moderation_summary__demo_seed__namespace=DEMO_NAMESPACE)
            | Q(title__in=demo_titles, user__email__in=demo_emails)
        )
        project_count = projects.count()
        projects.delete()
        user_count, _ = User.objects.filter(email__in=demo_emails).delete()
        self.stdout.write(f"Reset demo data: deleted {project_count} projects and {user_count} user/profile rows.")

    def _seed_users(self) -> dict[str, User]:
        users: dict[str, User] = {}
        for spec in (*PUBLISHERS, *STUDENTS, STAFF):
            username = spec.email
            first_name, last_name = _split_display_name(spec.display_name)
            user, _created = User.objects.update_or_create(
                username=username,
                defaults={
                    "email": spec.email,
                    "first_name": first_name,
                    "last_name": last_name,
                    "is_staff": spec.is_staff,
                    "is_active": True,
                },
            )
            user.set_password(DEMO_PASSWORD)
            user.save(update_fields=["password", "email", "first_name", "last_name", "is_staff", "is_active"])
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = spec.role
            profile.display_name = spec.display_name
            profile.bio = spec.bio
            profile.is_public_profile = spec.role in {"teacher", "publisher"}
            profile.save(update_fields=["role", "display_name", "bio", "is_public_profile", "updated_at"])
            users[spec.email] = user
        return users

    def _seed_lessons(
        self,
        users: dict[str, User],
        *,
        include_moderation: bool,
    ) -> dict[str, Project]:
        specs = list(LESSONS)
        if include_moderation:
            specs.extend(MODERATION_FIXTURES)

        projects: dict[str, Project] = {}
        video_bytes_by_duration: dict[int, bytes | None] = {}
        for index, spec in enumerate(specs):
            owner = users[spec.owner_email]
            category = _category_for(spec)
            project = _project_for(owner, spec)
            project.category = category
            project.description = spec.description
            project.moderation_status = spec.moderation_status
            project.moderation_summary = _moderation_summary_for(spec)
            project.avatar_enabled_override = False
            project.avatar_processing_status = "none"
            _sync_pages(project, spec)
            duration_seconds = _demo_lesson_duration_seconds(spec)
            duration_key = int(math.ceil(duration_seconds))
            if duration_key not in video_bytes_by_duration:
                video_bytes_by_duration[duration_key] = _generate_demo_video_bytes(duration_seconds)
            demo_video_bytes = video_bytes_by_duration[duration_key]
            playback_ready = _sync_demo_playback(project, demo_video_bytes)
            project.status = "ready" if playback_ready else "draft"
            project.is_published = bool(spec.published and playback_ready)
            project.save(
                update_fields=[
                    "category",
                    "description",
                    "status",
                    "is_published",
                    "moderation_status",
                    "moderation_summary",
                    "avatar_enabled_override",
                    "avatar_processing_status",
                    "updated_at",
                ]
            )
            _set_created_at(project, order_index=index + 1)
            projects[spec.key] = project
        if projects and not any(project.is_published for project in projects.values()):
            self.stdout.write(
                self.style.WARNING(
                    "Demo playback fixtures unavailable because ffmpeg could not generate them. "
                    "Demo lessons will remain unpublished and non-playable."
                )
            )
        return projects

    def _seed_analytics(self, users: dict[str, User], projects: dict[str, Project]) -> dict[str, int]:
        now = timezone.now()
        progress_count = 0
        like_count = 0
        comment_count = 0

        for lesson_key, user_progress in PROGRESS_ACTIVITY.items():
            project = projects.get(lesson_key)
            if project is None:
                continue
            for offset, (email, value) in enumerate(user_progress.items()):
                progress, _ = LessonProgress.objects.update_or_create(
                    user=users[email],
                    project=project,
                    defaults={"progress_pct": int(value)},
                )
                LessonProgress.objects.filter(pk=progress.pk).update(updated_at=now - timedelta(days=offset + 1))
                progress_count += 1

        for lesson_key, emails in LIKE_ACTIVITY.items():
            project = projects.get(lesson_key)
            if project is None:
                continue
            for offset, email in enumerate(emails):
                like, _ = LessonLike.objects.get_or_create(user=users[email], project=project)
                LessonLike.objects.filter(pk=like.pk).update(created_at=now - timedelta(days=offset + 2))
                like_count += 1

        for lesson_key, rows in COMMENT_ACTIVITY.items():
            project = projects.get(lesson_key)
            if project is None:
                continue
            for offset, (email, text) in enumerate(rows):
                comment, _ = LessonComment.objects.get_or_create(
                    user=users[email],
                    project=project,
                    text=text,
                )
                LessonComment.objects.filter(pk=comment.pk).update(created_at=now - timedelta(days=offset + 1))
                comment_count += 1

        return {"progress": progress_count, "likes": like_count, "comments": comment_count}

    def _ensure_ocr_fixture_image(self, projects: dict[str, Project]) -> None:
        project = projects.get("moderation-ocr-text-image")
        if project is None:
            return
        image_rel_path = "demo_seed/moderation_ocr_fixture.png"
        image_abs_path = Path(getattr(settings, "STORAGE_ROOT", "storage_local")) / image_rel_path
        try:
            _write_ocr_fixture_image(image_abs_path)
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(f"OCR fixture image generation skipped: {exc.__class__.__name__}: {str(exc)[:160]}")
            return

        page = project.transcript_pages.order_by("order", "id").first()
        if page is None:
            return
        editor_document = dict(page.editor_document or {})
        scene = dict(editor_document.get("scene") or {})
        scene.update(
            {
                "background_mode": "custom",
                "background_fit": "contain",
                "custom_background_path": image_rel_path,
            }
        )
        editor_document["scene"] = scene
        page.editor_document = editor_document
        page.save(update_fields=["editor_document", "updated_at"])

    def _run_moderation(self, projects: dict[str, Project]) -> list[dict[str, Any]]:
        _ensure_services_on_path()
        try:
            from ai_agents.models import AdminReviewRequest, AgentFinding, AgentRun
            from worker.ai_agents.orchestrator import ModerationOrchestrator
            from worker.ai_agents.ocr_bridge import OCRBridge
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(f"moderation provider unavailable: {exc.__class__.__name__}: {str(exc)[:160]}")
            return []

        results: list[dict[str, Any]] = []
        fixture_keys = [fixture.key for fixture in MODERATION_FIXTURES if fixture.key in projects]
        for key in fixture_keys:
            project = projects[key]
            if key == "moderation-ocr-text-image":
                results.append(_run_ocr_probe(project, OCRBridge))
                continue

            demo_meta = _demo_project_meta(project)
            result = ModerationOrchestrator().run(
                project.id,
                triggered_by_user_id=project.user_id,
                phase="demo_seed_moderation",
            )
            project.refresh_from_db()
            if demo_meta:
                summary = dict(project.moderation_summary or {})
                summary["demo_seed"] = demo_meta
                project.moderation_summary = summary
                project.save(update_fields=["moderation_summary", "updated_at"])
            run = _latest_agent_run(project, AgentRun)
            review = _ensure_demo_review_request(project, AdminReviewRequest, run=run)
            finding_count = AgentFinding.objects.filter(run=run).count() if run else int(result.get("finding_count") or 0)
            results.append(
                {
                    "title": project.title,
                    "status": _moderation_status_label(project.moderation_status),
                    "moderation_status": project.moderation_status,
                    "run_id": run.id if run else result.get("run_id"),
                    "finding_count": finding_count,
                    "review_id": review.id if review else "",
                }
            )
        return results

    def _run_intelligence(self, projects: dict[str, Project], users: dict[str, User]) -> list[dict[str, Any]]:
        try:
            from core.views import schedule_creator_analytics_intelligence, schedule_lesson_intelligence
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(f"intelligence scheduling unavailable: {exc.__class__.__name__}: {str(exc)[:160]}")
            return []

        results: list[dict[str, Any]] = []
        for key, project in projects.items():
            if key in MODERATION_FIXTURE_KEYS:
                continue
            result = schedule_lesson_intelligence(
                project.id,
                reason="demo_seed",
                requested_by_id=project.user_id,
                force=False,
            )
            results.append({"type": "lesson", "key": key, **result})

        for spec in PUBLISHERS:
            user = users[spec.email]
            result = schedule_creator_analytics_intelligence(user.id, reason="demo_seed", force=False)
            results.append({"type": "analytics", "email": spec.email, **result})
        return results

    def _print_summary(
        self,
        *,
        users: dict[str, User],
        projects: dict[str, Project],
        analytics_counts: dict[str, int],
        include_moderation: bool,
        moderation_results: list[dict[str, Any]],
        intelligence_results: list[dict[str, Any]],
    ) -> None:
        lesson_projects = [project for project in projects.values() if not _demo_project_meta(project).get("moderation_fixture")]
        moderation_projects = [project for project in projects.values() if _demo_project_meta(project).get("moderation_fixture")]

        self.stdout.write("")
        self.stdout.write("VISUS VidLab demo seed complete")
        self.stdout.write(f"Local demo password for all demo accounts: {DEMO_PASSWORD}")
        self.stdout.write("Demo users:")
        for email in sorted(users):
            user = users[email]
            role = getattr(getattr(user, "profile", None), "role", "")
            staff = " staff" if user.is_staff else ""
            self.stdout.write(f"  {email} ({role}{staff})")

        self.stdout.write("Lessons:")
        for project in sorted(lesson_projects, key=lambda item: item.title):
            meta = _demo_project_meta(project)
            page_count = project.transcript_pages.filter(is_active=True).count()
            self.stdout.write(
                f"  {project.id}: {project.title} [{meta.get('language')}, {meta.get('quality')}, {page_count} pages]"
            )

        self.stdout.write(
            "Analytics activity: "
            f"{analytics_counts['progress']} progress rows, {analytics_counts['likes']} likes, "
            f"{analytics_counts['comments']} comments."
        )

        if include_moderation:
            self.stdout.write("Moderation fixtures:")
            for project in sorted(moderation_projects, key=lambda item: item.title):
                meta = _demo_project_meta(project)
                self.stdout.write(
                    f"  {project.id}: {project.title} expected={meta.get('expected_moderation', 'unknown')} "
                    f"status={project.moderation_status}"
                )
        if moderation_results:
            self.stdout.write("Moderation smoke results:")
            for item in moderation_results:
                self.stdout.write(
                    "  "
                    f"{item['title']}: {item.get('status', 'unknown')} "
                    f"(project_status={item.get('moderation_status', 'unknown')}, "
                    f"run={item.get('run_id') or ''}, findings={item.get('finding_count', 0)}, "
                    f"review={item.get('review_id') or ''})"
                )

        if intelligence_results:
            self.stdout.write("Intelligence scheduling results:")
            for item in intelligence_results:
                target = item.get("key") or item.get("email") or item.get("project_id") or item.get("user_id")
                self.stdout.write(
                    f"  {item.get('type')}: {target} status={item.get('status')} report={item.get('report_id', '')}"
                )

        self.stdout.write("Generated media/database files are local runtime artifacts only. Do not commit db.sqlite3, media, or storage_local.")


def _category_for(spec: DemoLesson) -> Category:
    category, _ = Category.objects.update_or_create(
        slug=spec.category_slug,
        defaults={
            "name": spec.category,
            "description": f"Demo category for {spec.category.lower()} lessons.",
        },
    )
    return category


def _project_for(owner: User, spec: DemoLesson) -> Project:
    project = Project.objects.filter(user=owner, title=spec.title).order_by("id").first()
    if project is not None:
        return project
    return Project.objects.create(user=owner, title=spec.title)


def _sync_pages(project: Project, spec: DemoLesson) -> None:
    expected_keys: list[str] = []
    for index, page in enumerate(spec.pages):
        page_key = f"{spec.key}-page-{index + 1:02d}"
        expected_keys.append(page_key)
        narration = page.narration.strip()
        original = f"{page.title}\n\n{narration}"
        TranscriptPage.objects.update_or_create(
            project=project,
            page_key=page_key,
            defaults={
                "order": index,
                "source_slide_index": index,
                "split_index": 0,
                "original_text": original,
                "narration_text": narration,
                "rich_text_html": _rich_text_html(page.title, narration),
                "editor_document": _editor_document(page.title, narration, spec=spec),
                "subtitle_chunks": _subtitle_chunks(narration),
                "chunk_timeline": [],
                "whiteboard_mode": True,
                "is_active": True,
                "deleted_at": None,
                "start_seconds": float(index * DEMO_PAGE_DURATION_SECONDS),
                "end_seconds": float((index + 1) * DEMO_PAGE_DURATION_SECONDS),
                "duration_seconds": DEMO_PAGE_DURATION_SECONDS,
            },
        )
    project.transcript_pages.exclude(page_key__in=expected_keys).delete()


def _demo_lesson_duration_seconds(spec: DemoLesson) -> float:
    return max(DEMO_PAGE_DURATION_SECONDS, len(spec.pages) * DEMO_PAGE_DURATION_SECONDS)


def _sync_demo_playback(project: Project, video_bytes: bytes | None) -> bool:
    jobs = list(project.jobs.filter(job_type="video_export").order_by("id"))
    result_url = f"{project.id}/{DEMO_VIDEO_FILENAME}"
    if video_bytes is None:
        _mark_demo_playback_unavailable(project, jobs, "Demo playback fixture unavailable.")
        return False

    try:
        get_storage_adapter(getattr(settings, "STORAGE_ROOT", "storage_local")).write_bytes(result_url, video_bytes)
    except Exception:  # noqa: BLE001
        _mark_demo_playback_unavailable(project, jobs, "Demo playback fixture could not be stored.")
        return False

    if jobs:
        job = jobs[0]
        job.status = "done"
        job.progress = 100
        job.result_url = result_url
        job.srt_url = ""
        job.error_message = ""
        job.save(update_fields=["status", "progress", "result_url", "srt_url", "error_message", "updated_at"])
        if len(jobs) > 1:
            Job.objects.filter(pk__in=[extra.pk for extra in jobs[1:]]).delete()
    else:
        Job.objects.create(
            project=project,
            job_type="video_export",
            status="done",
            progress=100,
            result_url=result_url,
        )
    return True


def _mark_demo_playback_unavailable(project: Project, jobs: list[Job], message: str) -> None:
    job = jobs[0] if jobs else Job(project=project, job_type="video_export")
    job.status = "failed"
    job.progress = 0
    job.result_url = ""
    job.srt_url = ""
    job.error_message = message
    job.save()
    if len(jobs) > 1:
        Job.objects.filter(pk__in=[extra.pk for extra in jobs[1:]]).delete()


def _generate_demo_video_bytes(duration_seconds: float) -> bytes | None:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return None
    duration_seconds = max(1.0, float(duration_seconds or 0.0))

    with tempfile.TemporaryDirectory(prefix="visus-demo-video-") as temp_dir:
        output_path = Path(temp_dir) / DEMO_VIDEO_FILENAME
        command = [
            ffmpeg_path,
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x171923:s=320x180:r=1:d={duration_seconds:.3f}",
            "-t",
            f"{duration_seconds:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "35",
            "-profile:v",
            "baseline",
            "-level",
            "3.0",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-movflags",
            "+faststart",
            "-metadata",
            "creation_time=1970-01-01T00:00:00Z",
            "-y",
            str(output_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=60)
            payload = output_path.read_bytes()
        except (OSError, subprocess.SubprocessError):
            return None

    if len(payload) < 32 or b"ftyp" not in payload[:32]:
        return None
    return payload


def _moderation_summary_for(spec: DemoLesson) -> dict[str, Any]:
    status = spec.moderation_status
    message = (
        "Demo moderation fixture is ready for local scan."
        if spec.moderation_fixture
        else "Demo lesson pre-approved for local catalog and analytics smoke testing."
    )
    return {
        "moderation_status": status,
        "message": message,
        "demo_seed": {
            "namespace": DEMO_NAMESPACE,
            "key": spec.key,
            "language": spec.language,
            "quality": spec.quality,
            "moderation_fixture": spec.moderation_fixture,
            "expected_moderation": spec.expected_moderation,
        },
    }


def _editor_document(title: str, narration: str, *, spec: DemoLesson) -> dict[str, Any]:
    return {
        "version": 1,
        "paragraphs": [
            {"index": 0, "type": "heading", "text": title},
            {"index": 1, "type": "body", "text": narration},
        ],
        "scene": {
            "background_mode": "whiteboard",
            "background_fit": "contain",
            "text_scale": 1.0,
            "demo_seed": True,
            "language": spec.language,
        },
    }


def _rich_text_html(title: str, narration: str) -> str:
    safe_title = html.escape(title)
    safe_body = html.escape(narration).replace("\n", "<br />")
    return f"<h2>{safe_title}</h2><p>{safe_body}</p>"


def _subtitle_chunks(narration: str) -> list[str]:
    sentences = [part.strip() for part in narration.replace("?", ".").replace("!", ".").split(".") if part.strip()]
    chunks = []
    for sentence in sentences[:4]:
        chunks.append(sentence if sentence.endswith(".") else f"{sentence}.")
    return chunks or [narration[:180]]


def _set_created_at(project: Project, *, order_index: int) -> None:
    created_at = timezone.now() - timedelta(minutes=order_index * 7)
    Project.objects.filter(pk=project.pk).update(created_at=created_at, updated_at=created_at)


def _write_ocr_fixture_image(path: Path) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("PIL/Pillow is unavailable") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (900, 360), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    try:
        font_title = ImageFont.truetype("arial.ttf", 38)
        font_body = ImageFont.truetype("arial.ttf", 30)
    except Exception:
        font_title = ImageFont.load_default()
        font_body = ImageFont.load_default()
    draw.rectangle((24, 24, 876, 336), outline=(30, 80, 140), width=4)
    draw.text((64, 92), "MODERATION TEST: review this text", fill=(10, 40, 80), font=font_title)
    draw.text((64, 170), "Generated locally for OCR moderation smoke.", fill=(60, 60, 60), font=font_body)
    draw.text((64, 230), "No graphic or explicit content.", fill=(60, 60, 60), font=font_body)
    image.save(path)


def _run_ocr_probe(project: Project, ocr_bridge_cls) -> dict[str, Any]:
    page = project.transcript_pages.order_by("order", "id").first()
    scene = {}
    if page and isinstance(page.editor_document, dict):
        scene = dict(page.editor_document.get("scene") or {})
    rel_path = str(scene.get("custom_background_path") or "")
    image_path = Path(getattr(settings, "STORAGE_ROOT", "storage_local")) / rel_path if rel_path else None

    try:
        result = ocr_bridge_cls().extract(
            image_path=str(image_path or ""),
            asset_type="ocr_text",
            slide_order=0,
            project_id=project.id,
            ui_anchor=f"demo-ocr-project-{project.id}",
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "title": project.title,
            "status": "unknown",
            "moderation_status": project.moderation_status,
            "run_id": "",
            "finding_count": 0,
            "message": f"moderation provider unavailable: {exc.__class__.__name__}",
        }

    text = str(getattr(result, "text", "") or "").strip()
    if not text:
        return {
            "title": project.title,
            "status": "unknown",
            "moderation_status": project.moderation_status,
            "run_id": "",
            "finding_count": 0,
            "message": "moderation provider unavailable",
        }

    from worker.ai_agents.policy_engine import PolicyEngine
    from worker.ai_agents.providers.local_rules_provider import LocalRulesProvider

    findings = LocalRulesProvider().scan_text(text, result.location)
    decision = PolicyEngine().combine_findings(findings)
    return {
        "title": project.title,
        "status": _decision_status_label(decision),
        "moderation_status": project.moderation_status,
        "run_id": "",
        "finding_count": len(findings),
    }


def _latest_agent_run(project: Project, agent_run_model):
    if project.last_moderation_run_id:
        run = agent_run_model.objects.filter(pk=project.last_moderation_run_id, project=project).first()
        if run is not None:
            return run
    return agent_run_model.objects.filter(project=project, purpose="moderation").order_by("-created_at", "-id").first()


def _ensure_demo_review_request(project: Project, admin_review_model, *, run):
    if project.moderation_status not in {"revision_required", "needs_admin_review", "admin_rejected", "failed"}:
        return None
    meta = _demo_project_meta(project)
    message = (
        f"Demo moderation fixture for {meta.get('expected_moderation', 'review')} outcome. "
        "Safe local fixture; please review scan behavior for the demo."
    )
    review = admin_review_model.objects.filter(project=project, status="open").first()
    if review is None:
        return admin_review_model.objects.create(
            project=project,
            run=run,
            requested_by=project.user,
            publisher_message=message,
            status="open",
        )

    update_fields: list[str] = []
    run_id = getattr(run, "id", None)
    if run_id and review.run_id != run_id:
        review.run = run
        update_fields.append("run")
    if review.requested_by_id != project.user_id:
        review.requested_by = project.user
        update_fields.append("requested_by")
    if review.publisher_message != message:
        review.publisher_message = message
        update_fields.append("publisher_message")
    if update_fields:
        review.save(update_fields=update_fields)
    return review


def _moderation_status_label(status: str) -> str:
    if status in {"approved", "admin_approved"}:
        return "allowed"
    if status in {"needs_admin_review", "pending"}:
        return "needs_review"
    if status in {"revision_required", "admin_rejected"}:
        return "blocked"
    return "unknown"


def _decision_status_label(decision: str) -> str:
    if decision in {"allow", "warn"}:
        return "allowed"
    if decision == "needs_admin_review":
        return "needs_review"
    if decision == "block":
        return "blocked"
    return "unknown"


def _demo_project_meta(project: Project) -> dict[str, Any]:
    summary = project.moderation_summary if isinstance(project.moderation_summary, dict) else {}
    meta = summary.get("demo_seed")
    return dict(meta) if isinstance(meta, dict) else {}


def _split_display_name(display_name: str) -> tuple[str, str]:
    parts = str(display_name or "").split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _demo_emails() -> list[str]:
    return [spec.email for spec in (*PUBLISHERS, *STUDENTS, STAFF)]


def _ensure_services_on_path() -> None:
    services_root = Path(__file__).resolve().parents[4]
    if str(services_root) not in sys.path:
        sys.path.insert(0, str(services_root))
